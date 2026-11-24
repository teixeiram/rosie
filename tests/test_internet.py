import time

import httpx

from rosie.sections.internet import Prom, build_tiles


def prom_with(series):
    """series: {promql: [(labels, value), ...]}"""

    def handler(req: httpx.Request):
        q = req.url.params["query"]
        result = [{"metric": m, "value": [0, str(v)]} for m, v in series.get(q, [])]
        return httpx.Response(200, json={"status": "success", "data": {"result": result}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


ON_5G = (
    'max(unpoller_device_wan_status{wan_networkgroup="WAN3"}) * on() '
    '(1 - max(unpoller_device_wan_status{wan_networkgroup="WAN"}))'
)
FI_USED = (
    "sum(increase(unpoller_device_mbb_sim_receive_bytes_total[24h]))"
    " + sum(increase(unpoller_device_mbb_sim_transmit_bytes_total[24h]))"
)


def healthy(now):
    checks = [
        "internet",
        "modem",
        "router",
        "dns-router",
        "dns-cloudflare",
        "dns-system",
        "http-google",
        "isp-hop",
    ]
    return {
        "netcheck_check_status": [({"check": c}, 0) for c in checks],
        "max(netcheck_last_run_timestamp_seconds)": [({}, now - 60)],
        'probe_success{job=~"blackbox_icmp|blackbox_connectivity"}': [
            ({"instance": "1.1.1.1"}, 1),
            ({"instance": "apple"}, 1),
        ],
        "max(modem_operational)": [({}, 1)],
        "max(modem_scrape_success)": [({}, 1)],
        "max(modem_downstream_channels)": [({}, 34)],
        "count(modem_downstream_locked == 1)": [({}, 34)],
        "max(modem_uptime_seconds)": [({}, 7200)],
        "max(unpoller_controller_up)": [({}, 1)],
        'max(unpoller_device_wan_status{wan_networkgroup="WAN"})': [({}, 1)],
        'max(unpoller_device_uptime_seconds{type="udm"})': [({}, 90000)],
        # 5G Backup on standby
        "min(unpoller_device_mbb_internet)": [({}, 1)],
        "min(unpoller_device_mbb_sim_active)": [({}, 1)],
        "max(unpoller_device_mbb_rsrp_dbm)": [({}, -88)],
        ON_5G: [({}, 0)],
        "max(wan_control_manual_5g)": [({}, 0)],
        "max(wan_control_auto_restore_timestamp_seconds)": [({}, 0)],
        "max(wan_control_restore_when_healthy)": [({}, 0)],
        FI_USED: [({}, 28e6)],
    }


async def test_all_green():
    now = time.time()
    async with prom_with(healthy(now)) as c:
        out = await build_tiles(Prom(c, "http://p"), "http://g")
    states = {t["id"]: t["state"] for t in out["tiles"]}
    assert states == {"internet": "ok", "modem": "ok", "router": "ok", "backup": "ok", "dns": "ok"}
    router = next(t for t in out["tiles"] if t["id"] == "router")
    assert router["label"] == "Router (UniFi Dream Router 7)"
    assert router["link"] == "http://g/d/unifi-gateway"
    backup = next(t for t in out["tiles"] if t["id"] == "backup")
    assert backup["summary"] == "Standby"
    assert (
        "Signal -88 dBm" in backup["details"] and "Fi data, last 24 h: 28 MB" in backup["details"]
    )
    assert backup["until"] is None
    assert not out["stale"]
    modem = next(t for t in out["tiles"] if t["id"] == "modem")
    assert "34 of 34 downstream channels locked" in modem["details"]
    assert modem["link"] == "http://g/d/cable-modem"


async def test_problems_show():
    now = time.time()
    s = healthy(now)
    s["netcheck_check_status"] = [
        ({"check": "internet"}, 2),
        ({"check": "modem"}, 0),
        ({"check": "router"}, 0),
        ({"check": "dns-router"}, 1),
    ]
    s["count(modem_downstream_locked == 1)"] = [({}, 30)]
    s['max(unpoller_device_wan_status{wan_networkgroup="WAN"})'] = [({}, 0)]
    s[ON_5G] = [({}, 1)]
    async with prom_with(s) as c:
        out = await build_tiles(Prom(c, "http://p"), "http://g")
    states = {t["id"]: t["state"] for t in out["tiles"]}
    assert states == {
        "internet": "bad",
        "modem": "warn",
        "router": "warn",
        "backup": "warn",
        "dns": "warn",
    }
    router = next(t for t in out["tiles"] if t["id"] == "router")
    assert "Router says cable internet is offline" in router["details"]
    backup = next(t for t in out["tiles"] if t["id"] == "backup")
    assert backup["summary"] == "In use: cable is down"


async def test_backup_manual_override_and_not_ready():
    s = healthy(time.time())
    s["max(wan_control_manual_5g)"] = [({}, 1)]
    s["max(wan_control_auto_restore_timestamp_seconds)"] = [({}, 1795403640)]
    s["max(wan_control_restore_when_healthy)"] = [({}, 1)]
    async with prom_with(s) as c:
        out = await build_tiles(Prom(c, "http://p"), "http://g")
    backup = next(t for t in out["tiles"] if t["id"] == "backup")
    assert backup["state"] == "warn" and backup["summary"] == "In use: work laptops moved to 5G"
    assert backup["until"] == 1795403640
    assert "Back to cable once the modem is healthy" in backup["details"]

    s["min(unpoller_device_mbb_internet)"] = [({}, 0)]
    async with prom_with(s) as c:
        out = await build_tiles(Prom(c, "http://p"), "http://g")
    backup = next(t for t in out["tiles"] if t["id"] == "backup")
    assert backup["state"] == "bad" and backup["summary"] == "Not ready"


async def test_stale_data_is_unknown():
    s = healthy(time.time() - 3600)
    async with prom_with(s) as c:
        out = await build_tiles(Prom(c, "http://p"), "http://g")
    # netcheck-based tiles go unknown; the 5G Backup tile comes from UnPoller.
    assert out["stale"]
    assert all(t["state"] == "unknown" for t in out["tiles"] if t["id"] != "backup")


def test_endpoint_error_is_contained(app, member):
    app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    r = member.get("/api/internet/status")
    assert r.status_code == 500
    assert member.get("/api/kitchen/status").status_code == 200
