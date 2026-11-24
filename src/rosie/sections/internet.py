"""Internet status: modem, router, internet and DNS tiles from Prometheus (issue #6).

Read-only for everyone. The data comes from the home-monitoring stack:
netcheck (node-exporter textfile), modem-exporter, UnPoller (UniFi Dream
Router 7 and its 5G Backup), wan-control (the manual 5G
switch) and the blackbox probes. The router source is a small table below.

Netcheck statuses: 0 = ok, 1 = degraded, 2 = failed.
"""

from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Depends, Request

from ..auth import User, current_user

SECTION = {"id": "internet", "title": "Internet", "admin_only": False}
router = APIRouter(prefix="/api/internet", tags=["internet"])

STALE_AFTER = 15 * 60  # netcheck runs every few minutes; older data is "unknown"

# Router data sources. Each maps tile facts to PromQL.
ROUTERS = {
    "unifi": {
        "label": "Router (UniFi Dream Router 7)",
        "up": "unpoller_controller_up",
        "wan_online": 'unpoller_device_wan_status{wan_networkgroup="WAN"}',
        "wan_link": "",  # not exported; a dead cable shows as WAN offline
        "uptime": 'unpoller_device_uptime_seconds{type="udm"}',
        "dashboard": "unifi-gateway",
    },
    "nest": {
        "label": "Router (Nest Wifi)",
        "up": "nest_up",
        "wan_online": "nest_wan_online",
        "wan_link": "nest_wan_ethernet_link",
        "uptime": "nest_uptime_seconds",
        "dashboard": "nest-router",
    },
}
ROUTER_SOURCE = "unifi"


class Prom:
    def __init__(self, client: httpx.AsyncClient, base: str):
        self.client, self.base = client, base

    async def query(self, q: str) -> list[dict]:
        r = await self.client.get(f"{self.base}/api/v1/query", params={"query": q}, timeout=8)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "success":
            raise RuntimeError("Prometheus query failed")
        return data["data"]["result"]

    async def scalar(self, q: str) -> float | None:
        res = await self.query(q)
        return float(res[0]["value"][1]) if res else None

    async def by_label(self, q: str, label: str) -> dict[str, float]:
        return {r["metric"].get(label, ""): float(r["value"][1]) for r in await self.query(q)}


def _state(status: float | None) -> str:
    if status is None:
        return "unknown"
    return {0: "ok", 1: "warn"}.get(int(status), "bad")


def _ago(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = int(seconds)
    if s < 3600:
        return f"{s // 60} min"
    if s < 86400:
        return f"{s // 3600} h"
    return f"{s // 86400} days"


def _worst(*states: str) -> str:
    order = ["bad", "warn", "unknown", "ok"]
    return min(states, key=order.index) if states else "unknown"


async def build_tiles(prom: Prom, grafana: str) -> dict:
    checks = await prom.by_label("netcheck_check_status", "check")
    last_run = await prom.scalar("max(netcheck_last_run_timestamp_seconds)")
    now = time.time()
    stale = last_run is None or now - last_run > STALE_AFTER

    def chk(name: str) -> str:
        return "unknown" if stale else _state(checks.get(name))

    tiles = []

    # Internet: netcheck's end-to-end check plus the phones' connectivity probes.
    probes = await prom.by_label(
        'probe_success{job=~"blackbox_icmp|blackbox_connectivity"}', "instance"
    )
    failing = [k for k, v in probes.items() if v < 1]
    st = chk("internet")
    if st == "ok" and failing:
        st = "warn"
    details = [f"{len(probes) - len(failing)} of {len(probes)} outside checks passing"]
    for name, label in (
        ("http-google", "Google"),
        ("http-youtube", "YouTube"),
        ("http-nearpod", "Nearpod"),
        ("isp-hop", "ISP first hop"),
    ):
        if name in checks and checks[name] != 0:
            details.append(f"{label}: {'slow' if checks[name] == 1 else 'failing'}")
    tiles.append(
        {
            "id": "internet",
            "label": "Internet",
            "state": st,
            "summary": {
                "ok": "Working",
                "warn": "Partly working",
                "bad": "Down",
                "unknown": "No recent data",
            }[st],
            "details": details,
            "link": f"{grafana}/d/network-path",
        }
    )

    # Cable modem.
    operational = await prom.scalar("max(modem_operational)")
    scrape_ok = await prom.scalar("max(modem_scrape_success)")
    channels = await prom.scalar("max(modem_downstream_channels)")
    locked = await prom.scalar("count(modem_downstream_locked == 1)")
    uptime = await prom.scalar("max(modem_uptime_seconds)")
    st = chk("modem")
    if st == "ok" and (operational == 0 or (channels and locked is not None and locked < channels)):
        st = "warn"
    if scrape_ok == 0 and st == "ok":
        st = "warn"
    details = []
    if channels:
        details.append(f"{int(locked or 0)} of {int(channels)} downstream channels locked")
    if uptime is not None:
        details.append(f"Up {_ago(uptime)}")
    if scrape_ok == 0:
        details.append("Modem status page not answering")
    tiles.append(
        {
            "id": "modem",
            "label": "Cable modem",
            "state": st,
            "summary": {
                "ok": "Online",
                "warn": "Online with problems",
                "bad": "Not reachable",
                "unknown": "No recent data",
            }[st],
            "details": details,
            "link": f"{grafana}/d/cable-modem",
        }
    )

    # Router.
    r = ROUTERS[ROUTER_SOURCE]
    up = await prom.scalar(f"max({r['up']})")
    wan_online = await prom.scalar(f"max({r['wan_online']})")
    wan_link = await prom.scalar(f"max({r['wan_link']})") if r["wan_link"] else None
    r_uptime = await prom.scalar(f"max({r['uptime']})")
    st = chk("router")
    if st == "ok" and (wan_online == 0 or wan_link == 0):
        st = "bad" if wan_link == 0 else "warn"
    details = []
    if wan_link == 0:
        details.append("Cable to the modem is unplugged or dead")
    elif wan_online == 0:
        details.append("Router says cable internet is offline")
    if r_uptime is not None:
        details.append(f"Up {_ago(r_uptime)}")
    if up == 0:
        details.append("Router status API not answering")
    tiles.append(
        {
            "id": "router",
            "label": r["label"],
            "state": st,
            "summary": {
                "ok": "Online",
                "warn": "Online with problems",
                "bad": "Problem",
                "unknown": "No recent data",
            }[st],
            "details": details,
            "link": f"{grafana}/d/{r['dashboard']}",
        }
    )

    # 5G Backup (UniFi 5G Backup on a Google Fi SIM): standby, in use, or not ready.
    if ROUTER_SOURCE == "unifi":
        tiles.append(await backup_tile(prom, grafana))

    # DNS (name lookups).
    dns = [chk(n) for n in ("dns-router", "dns-cloudflare", "dns-system")]
    st = _worst(*dns)
    tiles.append(
        {
            "id": "dns",
            "label": "Name lookups (DNS)",
            "state": st,
            "summary": {
                "ok": "Working",
                "warn": "Slow",
                "bad": "Failing",
                "unknown": "No recent data",
            }[st],
            "details": [],
            "link": f"{grafana}/d/network-path",
        }
    )

    return {"tiles": tiles, "checked_at": int(last_run) if last_run else None, "stale": stale}


async def backup_tile(prom: Prom, grafana: str) -> dict:
    ready = await prom.scalar("min(unpoller_device_mbb_internet)")
    sim = await prom.scalar("min(unpoller_device_mbb_sim_active)")
    rsrp = await prom.scalar("max(unpoller_device_mbb_rsrp_dbm)")
    on_5g = await prom.scalar(
        'max(unpoller_device_wan_status{wan_networkgroup="WAN3"}) * on() '
        '(1 - max(unpoller_device_wan_status{wan_networkgroup="WAN"}))'
    )
    manual = await prom.scalar("max(wan_control_manual_5g)")
    until = await prom.scalar("max(wan_control_auto_restore_timestamp_seconds)")
    waiting = await prom.scalar("max(wan_control_restore_when_healthy)")
    used = await prom.scalar(
        "sum(increase(unpoller_device_mbb_sim_receive_bytes_total[24h]))"
        " + sum(increase(unpoller_device_mbb_sim_transmit_bytes_total[24h]))"
    )
    if ready is None:
        st, summary = "unknown", "No data"
    elif ready < 1 or sim == 0:
        st, summary = "bad", "Not ready"
    elif on_5g == 1:
        st, summary = "warn", "In use: cable is down"
    elif manual == 1:
        st, summary = "warn", "In use: work laptops moved to 5G"
    else:
        st, summary = "ok", "Standby"
    details = []
    if manual == 1 and waiting == 1:
        details.append("Back to cable once the modem is healthy")
    if rsrp is not None:
        details.append(f"Signal {rsrp:.0f} dBm")
    if used is not None:
        details.append(f"Fi data, last 24 h: {used / 1e6:.0f} MB")
    return {
        "id": "backup",
        "label": "5G Backup",
        "state": st,
        "summary": summary,
        "details": details,
        "link": f"{grafana}/d/unifi-gateway",
        # The browser formats this in its own time zone ("back to cable by 10:14 PM").
        "until": int(until) if manual == 1 and until else None,
    }


@router.get("/status")
async def status(request: Request, user: User = Depends(current_user)) -> dict:
    s = request.app.state.settings
    return await build_tiles(Prom(request.app.state.http, s.prometheus_url), s.grafana_url)
