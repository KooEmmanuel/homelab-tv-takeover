#!/usr/bin/env python3
"""Find smart TVs on the home network. Standard library only, read-only.

Run on the Pi:  python3 tv-scan.py            (scans your own /24, e.g. 192.168.1.1-254)
                python3 tv-scan.py 192.168.1   (another /24)
                python3 tv-scan.py --json      (machine-readable output)

What it does:
  1. SSDP / DIAL M-SEARCH (Roku, Fire TV, Samsung, LG, Sony, Cast, UPnP renderers)
  2. mDNS PTR queries (Google Cast, AirPlay, Amazon, Android TV, Spotify Connect)
  3. TCP connect check of common TV control ports on every address
  4. Read-only GETs on the info endpoints each platform exposes (no key presses)
"""
import concurrent.futures as cf
import json
import plistlib
import re
import socket
import ssl
import struct
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

SUBNET = next((a for a in sys.argv[1:] if not a.startswith("-")), "192.168.1")
AS_JSON = "--json" in sys.argv
TIMEOUT = 2.0

SSDP_TARGETS = [
    "ssdp:all",
    "roku:ecp",
    "urn:dial-multiscreen-org:service:dial:1",
    "urn:samsung.com:device:RemoteControlReceiver:1",
    "urn:lge-com:service:webos-second-screen:1",
    "urn:schemas-sony-com:service:ScalarWebAPI:1",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
]

MDNS_SERVICES = [
    "_googlecast._tcp.local",
    "_airplay._tcp.local",
    "_raop._tcp.local",
    "_amzn-wplay._tcp.local",
    "_amzn-alexa._tcp.local",
    "_androidtvremote2._tcp.local",
    "_spotify-connect._tcp.local",
    "_hap._tcp.local",
]

# port -> what usually listens there
TV_PORTS = {
    8060: "Roku ECP",
    8008: "Google Cast (http)",
    8009: "Google Cast",
    8443: "Google Cast (https)",
    5555: "Android/Fire TV ADB",
    6466: "Android TV Remote v2",
    6467: "Android TV Remote pairing",
    7000: "AirPlay",
    8001: "Samsung Tizen WS",
    8002: "Samsung Tizen WSS",
    3000: "LG webOS WS",
    3001: "LG webOS WSS",
    1925: "Philips JointSpace",
    1926: "Philips JointSpace (https)",
    7345: "Vizio SmartCast",
    9000: "Vizio SmartCast (old)",
    20060: "Sony Bravia IRCC",
    60000: "Fire TV DIAL",
}

INSECURE = ssl.create_default_context()
INSECURE.check_hostname = False
INSECURE.verify_mode = ssl.CERT_NONE


def http_get(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": "homelab-tv-scan/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=INSECURE) as r:
            return r.read(256 * 1024), dict(r.headers)
    except Exception:
        return None, {}


# ---------------------------------------------------------------- SSDP

def ssdp_discover(wait=4.0):
    found = {}  # ip -> {"server":..., "locations":set(), "st":set()}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(0.3)
    for st in SSDP_TARGETS:
        msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
               'MAN: "ssdp:discover"\r\nMX: 2\r\nST: %s\r\n\r\n' % st).encode()
        for _ in range(2):
            sock.sendto(msg, ("239.255.255.250", 1900))
    end = time.time() + wait
    while time.time() < end:
        try:
            data, (ip, _) = sock.recvfrom(8192)
        except socket.timeout:
            continue
        headers = {}
        for line in data.decode(errors="replace").split("\r\n")[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        d = found.setdefault(ip, {"server": set(), "locations": set(), "st": set()})
        if headers.get("server"):
            d["server"].add(headers["server"])
        if headers.get("location"):
            d["locations"].add(headers["location"])
        if headers.get("st"):
            d["st"].add(headers["st"])
    sock.close()
    return found


def parse_upnp_description(xml_bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return {}
    out = {}
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag in ("friendlyName", "manufacturer", "modelName", "modelNumber",
                   "modelDescription", "deviceType") and el.text and tag not in out:
            out[tag] = el.text.strip()
    return out


# ---------------------------------------------------------------- mDNS

def _encode_name(name):
    return b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"


def _read_name(buf, off):
    labels, jumped, end = [], False, None
    for _ in range(64):
        n = buf[off]
        if n == 0:
            off += 1
            break
        if n & 0xC0 == 0xC0:
            if not jumped:
                end = off + 2
            off = ((n & 0x3F) << 8) | buf[off + 1]
            jumped = True
            continue
        labels.append(buf[off + 1:off + 1 + n].decode(errors="replace"))
        off += 1 + n
    return ".".join(labels), (end if jumped else off)


def _parse_mdns(buf):
    recs = []
    try:
        _, _, qd, an, ns, ar = struct.unpack(">HHHHHH", buf[:12])
        off = 12
        for _ in range(qd):
            _, off = _read_name(buf, off)
            off += 4
        for _ in range(an + ns + ar):
            name, off = _read_name(buf, off)
            rtype, _, _, rdlen = struct.unpack(">HHIH", buf[off:off + 10])
            off += 10
            rdata_off, rdata = off, buf[off:off + rdlen]
            off += rdlen
            if rtype == 12:  # PTR
                recs.append(("PTR", name, _read_name(buf, rdata_off)[0]))
            elif rtype == 1 and rdlen == 4:  # A
                recs.append(("A", name, socket.inet_ntoa(rdata)))
            elif rtype == 33:  # SRV
                port = struct.unpack(">H", rdata[4:6])[0]
                recs.append(("SRV", name, (_read_name(buf, rdata_off + 6)[0], port)))
            elif rtype == 16:  # TXT
                txt, i = {}, 0
                while i < len(rdata):
                    ln = rdata[i]
                    kv = rdata[i + 1:i + 1 + ln].decode(errors="replace")
                    i += 1 + ln
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        txt[k] = v
                recs.append(("TXT", name, txt))
    except Exception:
        pass
    return recs


def mdns_discover(wait=4.0):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    sock.settimeout(0.3)
    # one query per service, QU bit set so devices may answer us directly
    for svc in MDNS_SERVICES:
        q = struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0) + _encode_name(svc) + struct.pack(">HH", 12, 0x8001)
        for _ in range(2):
            sock.sendto(q, ("224.0.0.251", 5353))
    recs, src = [], {}
    end = time.time() + wait
    while time.time() < end:
        try:
            data, (ip, _) = sock.recvfrom(9000)
        except socket.timeout:
            continue
        for r in _parse_mdns(data):
            recs.append(r)
            src.setdefault(r[1], ip)
    sock.close()

    # stitch PTR -> instance -> SRV/TXT -> A
    a = {n: v for t, n, v in recs if t == "A"}
    srv = {n: v for t, n, v in recs if t == "SRV"}
    txt = {n: v for t, n, v in recs if t == "TXT"}
    out = {}  # ip -> list of {service, instance, txt}
    for t, n, inst in recs:
        if t != "PTR" or not any(n.endswith(s) for s in MDNS_SERVICES):
            continue
        host_port = srv.get(inst)
        ip = a.get(host_port[0]) if host_port else None
        ip = ip or src.get(inst) or src.get(n)
        if not ip:
            continue
        entry = {"service": n.replace(".local", ""), "instance": inst.split("._")[0],
                 "txt": txt.get(inst, {})}
        lst = out.setdefault(ip, [])
        if entry not in lst:
            lst.append(entry)
    return out


# ---------------------------------------------------------------- ports

def port_open(ip, port):
    try:
        with socket.create_connection((ip, port), timeout=0.5):
            return True
    except OSError:
        return False


def port_scan():
    targets = [(f"{SUBNET}.{i}", p) for i in range(1, 255) for p in TV_PORTS]
    open_ports = {}
    with cf.ThreadPoolExecutor(max_workers=256) as ex:
        for (ip, p), ok in zip(targets, ex.map(lambda t: port_open(*t), targets)):
            if ok:
                open_ports.setdefault(ip, []).append(p)
    return open_ports


# ---------------------------------------------------------------- fingerprints (read-only GETs)

def probe_roku(ip):
    body, _ = http_get(f"http://{ip}:8060/query/device-info")
    if not body:
        return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    g = lambda k: (root.findtext(k) or "").strip()
    return {
        "platform": "Roku",
        "name": g("user-device-name") or g("friendly-device-name"),
        "brand": g("vendor-name"),
        "model": " ".join(x for x in (g("model-name"), f"({g('model-number')})" if g("model-number") else "") if x),
        "is_tv": g("is-tv") == "true",
        "extra": {
            "os": g("software-version"), "power": g("power-mode"),
            "control_mode": g("ecp-setting-mode"),
            "network": g("network-type"), "screen": g("screen-size"),
        },
    }


def probe_cast(ip):
    body, _ = http_get(f"http://{ip}:8008/setup/eureka_info?options=detail")
    if not body:
        return None
    try:
        j = json.loads(body)
    except ValueError:
        return None
    det = j.get("device_info", {}) or {}
    return {
        "platform": "Google Cast",
        "name": j.get("name", ""),
        "brand": det.get("manufacturer", ""),
        "model": det.get("model_name", ""),
        "is_tv": None,
        "extra": {"build": j.get("cast_build_revision", "")},
    }


def probe_samsung(ip):
    body, _ = http_get(f"http://{ip}:8001/api/v2/")
    if not body:
        return None
    try:
        d = json.loads(body).get("device", {})
    except ValueError:
        return None
    return {
        "platform": "Samsung Tizen",
        "name": d.get("name", ""),
        "brand": "Samsung",
        "model": d.get("modelName", "") or d.get("model", ""),
        "is_tv": d.get("type", "").lower().endswith("tv"),
        "extra": {"os": d.get("OS", ""), "power": d.get("PowerState", ""),
                  "token_auth": d.get("TokenAuthSupport", "")},
    }


def probe_airplay(ip):
    body, _ = http_get(f"http://{ip}:7000/info")
    if not body:
        return None
    try:
        d = plistlib.loads(body)
    except Exception:
        return None
    return {
        "platform": "AirPlay",
        "name": d.get("name", ""),
        "brand": d.get("manufacturer", ""),
        "model": d.get("model", ""),
        "is_tv": None,
        "extra": {},
    }


def probe_vizio(ip):
    for port in (7345, 9000):
        body, _ = http_get(f"https://{ip}:{port}/state/device/deviceinfo")
        if body:
            try:
                item = json.loads(body)["ITEMS"][0]["VALUE"]
                return {"platform": "Vizio SmartCast", "name": item.get("NAME", ""),
                        "brand": "Vizio", "model": item.get("MODEL_NAME", ""),
                        "is_tv": True, "extra": {}}
            except Exception:
                pass
    return None


def probe_philips(ip):
    for url in (f"https://{ip}:1926/6/system", f"http://{ip}:1925/6/system"):
        body, _ = http_get(url)
        if body:
            try:
                d = json.loads(body)
                return {"platform": "Philips JointSpace", "name": d.get("name", ""),
                        "brand": "Philips", "model": d.get("model", ""),
                        "is_tv": True, "extra": {}}
            except ValueError:
                pass
    return None


def probe_upnp(locations):
    for loc in sorted(locations):
        body, headers = http_get(loc)
        if body:
            info = parse_upnp_description(body)
            if info:
                info["_location"] = loc
                info["_app_url"] = headers.get("Application-URL", "")
                return info
    return {}


# ---------------------------------------------------------------- glue

def my_ips():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("239.255.255.250", 1900))
        return {s.getsockname()[0]}
    except OSError:
        return set()


def silent_neighbours(seen):
    """Hosts in the ARP table that answered nothing: often a TV in deep sleep."""
    out = []
    try:
        for line in open("/proc/net/arp").read().splitlines()[1:]:
            f = line.split()
            if f[0].startswith(SUBNET + ".") and f[3] != "00:00:00:00:00:00" and f[0] not in seen:
                out.append((f[0], f[3]))
    except OSError:
        pass
    return sorted(out, key=lambda t: int(t[0].rsplit(".", 1)[1]))


TV_WORDS = re.compile(r"tv|roku|aft|fire|bravia|webos|tizen|smartcast|insignia|tcl|hisense|"
                      r"vizio|samsung|lg|sony|philips|sharp|toshiba|chromecast|android", re.I)

CONTROL = {
    8060: "Roku ECP (HTTP :8060, no pairing)",
    5555: "ADB over network (:5555)",
    6466: "Android TV Remote v2 (pairing code)",
    8001: "Samsung WebSocket remote (:8001/8002, allow prompt)",
    8002: "Samsung WebSocket remote (:8001/8002, allow prompt)",
    3000: "LG webOS SSAP (:3000/3001, pairing prompt)",
    3001: "LG webOS SSAP (:3000/3001, pairing prompt)",
    7345: "Vizio SmartCast API (PIN pairing)",
    9000: "Vizio SmartCast API (PIN pairing)",
    1926: "Philips JointSpace (PIN pairing)",
    20060: "Sony IRCC (PSK)",
    8009: "Google Cast (cast media / launch apps)",
    7000: "AirPlay (stream media)",
    60000: "DIAL (launch YouTube/Netflix)",
}


def main():
    log = (lambda *a: None) if AS_JSON else (lambda *a: print(*a, file=sys.stderr, flush=True))
    log(f"[1/4] SSDP/DIAL discovery ...")
    with cf.ThreadPoolExecutor(3) as ex:
        f_ssdp = ex.submit(ssdp_discover)
        f_mdns = ex.submit(mdns_discover)
        log(f"[2/4] mDNS queries ... (in parallel)")
        log(f"[3/4] Port check {SUBNET}.1-254 x {len(TV_PORTS)} ports ...")
        f_ports = ex.submit(port_scan)
        ssdp, mdns, ports = f_ssdp.result(), f_mdns.result(), f_ports.result()

    ips = sorted(set(ssdp) | set(mdns) | set(ports),
                 key=lambda s: tuple(int(x) for x in s.split(".")))
    ips = [ip for ip in ips if ip.startswith(SUBNET + ".") and ip not in my_ips()]
    log(f"[4/4] Reading device info from {len(ips)} responding hosts ...")

    def fingerprint(ip):
        op = set(ports.get(ip, []))
        probes = []
        if 8060 in op or any("roku" in s.lower() for s in ssdp.get(ip, {}).get("st", ())):
            probes.append(probe_roku)
        if 8008 in op or 8009 in op:
            probes.append(probe_cast)
        if 8001 in op:
            probes.append(probe_samsung)
        if 7000 in op:
            probes.append(probe_airplay)
        if op & {7345, 9000}:
            probes.append(probe_vizio)
        if op & {1925, 1926}:
            probes.append(probe_philips)
        hits = [r for r in (p(ip) for p in probes) if r]
        upnp = probe_upnp(ssdp.get(ip, {}).get("locations", set()))
        try:
            rdns = socket.gethostbyaddr(ip)[0]
        except OSError:
            rdns = ""
        return ip, hits, upnp, rdns

    with cf.ThreadPoolExecutor(32) as ex:
        results = list(ex.map(fingerprint, ips))

    devices = []
    for ip, hits, upnp, rdns in results:
        op = sorted(ports.get(ip, []))
        svc = mdns.get(ip, [])
        best = hits[0] if hits else {}
        txt = {k: v for s in svc for k, v in s["txt"].items()}
        name = (best.get("name") or upnp.get("friendlyName") or txt.get("fn")
                or (svc[0]["instance"] if svc else "") or rdns)
        brand = best.get("brand") or upnp.get("manufacturer") or txt.get("manufacturer", "")
        model = (best.get("model") or " ".join(x for x in (upnp.get("modelName"), upnp.get("modelNumber")) if x)
                 or txt.get("md") or txt.get("model", ""))
        blob = " ".join([name, brand, model, upnp.get("deviceType", ""), rdns,
                         " ".join(ssdp.get(ip, {}).get("server", ())),
                         " ".join(s["service"] for s in svc)])
        is_tv = best.get("is_tv")
        looks_tv = bool(is_tv) or (is_tv is None and (
            bool(set(op) & {8060, 5555, 6466, 8001, 3000, 3001, 7345, 1926, 20060, 60000})
            or bool(TV_WORDS.search(blob))))
        if not (op or svc or ssdp.get(ip)):
            continue
        control = []
        for p in op:
            c = CONTROL.get(p)
            if c and c not in control:
                control.append(c)
        if upnp.get("_app_url") and "DIAL (launch YouTube/Netflix)" not in control:
            control.append("DIAL (launch YouTube/Netflix)")
        devices.append({
            "ip": ip, "name": name, "brand": brand, "model": model,
            "platforms": [h["platform"] for h in hits], "looks_like_tv": looks_tv,
            "is_tv_reported": is_tv, "open_ports": op, "control": control,
            "mdns": [s["service"] for s in svc],
            "ssdp_server": sorted(ssdp.get(ip, {}).get("server", ())),
            "upnp": {k: v for k, v in upnp.items() if not k.startswith("_")},
            "extra": {k: v for h in hits for k, v in h.get("extra", {}).items() if v},
            "rdns": rdns,
        })

    if AS_JSON:
        print(json.dumps(devices, indent=2))
        return

    tvs = [d for d in devices if d["looks_like_tv"]]
    others = [d for d in devices if not d["looks_like_tv"]]
    print(f"\n=== Likely TVs / TV sticks: {len(tvs)} ===")
    for d in tvs:
        print(f"\n{d['ip']}  {d['name'] or '?'}")
        print(f"  brand/model : {d['brand'] or '?'} / {d['model'] or '?'}")
        if d["platforms"]:
            print(f"  platform    : {', '.join(d['platforms'])}")
        print(f"  open ports  : {', '.join(f'{p} ({TV_PORTS[p]})' for p in d['open_ports']) or '-'}")
        if d["mdns"]:
            print(f"  mDNS        : {', '.join(d['mdns'])}")
        if d["extra"]:
            print(f"  details     : {', '.join(f'{k}={v}' for k, v in d['extra'].items())}")
        print(f"  control via : {'; '.join(d['control']) or 'nothing open'}")
    print(f"\n=== Other devices that answered discovery: {len(others)} ===")
    for d in others:
        print(f"  {d['ip']:<15} {d['name'] or '?'}  [{d['brand'] or '?'} {d['model']}]".rstrip())
    quiet = silent_neighbours({d["ip"] for d in devices} | my_ips())
    print(f"\n=== In ARP table but silent (asleep TVs show up here): {len(quiet)} ===")
    for ip, mac in quiet:
        print(f"  {ip:<15} {mac}")


if __name__ == "__main__":
    main()
