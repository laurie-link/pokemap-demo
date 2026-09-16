const ICONS = {
  POKESTOP: L.icon({
    iconUrl: "/icons/pokestop.png",
    iconSize: [32, 53],
    iconAnchor: [16, 53],
    popupAnchor: [0, -46],
    className: "pogo-marker",
  }),
  GYM: L.icon({
    iconUrl: "/icons/gym.png",
    iconSize: [34, 48],
    iconAnchor: [17, 48],
    popupAnchor: [0, -42],
    className: "pogo-marker",
  }),
  POWERSPOT: L.icon({
    iconUrl: "/icons/powerspot.png",
    iconSize: [32, 53],
    iconAnchor: [16, 53],
    popupAnchor: [0, -46],
    className: "pogo-marker",
  }),
};

const TEAM_LABEL = {
  MYSTIC: "Mystic 神秘",
  VALOR: "Valor 勇气",
  INSTINCT: "Instinct 本能",
};

const $ = (id) => document.getElementById(id);
const map = L.map("map", { zoomControl: true }).setView([34.6664, 135.5006], 16);
L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
  { attribution: "Esri", maxZoom: 19 }
).addTo(map);

const overlays = L.layerGroup().addTo(map);
let lastResult = null;

function api() {
  return window.pywebview?.api;
}

function setBusy(on, text) {
  $("busy").hidden = !on;
  if (text) $("busy-text").textContent = text;
}

function setHint(text) {
  $("hint").textContent = text;
}

function wireNativeEdit(el) {
  el.addEventListener("keydown", async (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (!mod) return;
    const key = e.key.toLowerCase();
    if (key === "a") {
      e.preventDefault();
      e.stopPropagation();
      el.select();
      return;
    }
    if (key === "v") {
      e.preventDefault();
      e.stopPropagation();
      let text = "";
      try {
        if (api()?.get_clipboard) text = await api().get_clipboard();
      } catch (_) {}
      if (!text) {
        try {
          text = await navigator.clipboard.readText();
        } catch (_) {}
      }
      if (!text) return;
      const start = el.selectionStart ?? el.value.length;
      const end = el.selectionEnd ?? el.value.length;
      el.value = el.value.slice(0, start) + text + el.value.slice(end);
      const caret = start + text.length;
      el.setSelectionRange(caret, caret);
    }
  });
}

["coords", "radius"].forEach((id) => wireNativeEdit($(id)));

function zoomForRadius(m) {
  if (m <= 200) return 18;
  if (m <= 500) return 17;
  if (m <= 1200) return 16;
  if (m <= 2500) return 15;
  return 14;
}

function fmtTime(v) {
  if (v == null || v === "") return "";
  const n = Number(v);
  const d = Number.isFinite(n) ? new Date(n > 1e12 ? n : n * 1000) : new Date(v);
  if (Number.isNaN(d.getTime())) return String(v);
  return d.toLocaleString();
}

function visibleFilter(p) {
  if (p.entity === "POKESTOP") return $("f-stop").checked;
  if (p.entity === "ROUTE") return $("f-route").checked;
  if (p.entity === "EVENT") {
    if ($("f-event").checked) return true;
    if (p.superMega && $("f-mega").checked) return true;
    return false;
  }
  if (p.entity === "POWERSPOT") {
    if ($("f-power").checked) return true;
    if (p.gmax && $("f-gmax").checked) return true;
    if (p.dmax && $("f-dmax").checked) return true;
    return false;
  }
  if (p.entity === "GYM") {
    if ($("f-gym").checked) return true;
    if (p.raid && $("f-raid").checked) return true;
    if (p.superMega && $("f-mega").checked) return true;
    return false;
  }
  return false;
}

function popupHtml(p) {
  const bits = [];
  bits.push(`<b>${p.entity}</b>`);
  if (p.name) bits.push(p.name);
  if (p.team) {
    const cls = `team-${String(p.team).toLowerCase()}`;
    bits.push(`阵营: <span class="${cls}">${TEAM_LABEL[p.team] || p.team}</span>`);
  }
  if (p.megaEligible) bits.push("Super Mega Raid 可能出现");
  if (p.raid) {
    const r = p.raid;
    const mega = r.megaEnhanced || r.mega;
    bits.push(
      `<span class="raid-badge${mega ? " mega-badge" : ""}">${mega ? "SUPER MEGA" : "RAID"} ${r.rating || ""}</span> ${r.bossName || "蛋"}`
    );
    if (r.hatchTime) bits.push(`孵化: ${fmtTime(r.hatchTime)}`);
    if (r.startTime) bits.push(`开始: ${fmtTime(r.startTime)}`);
    if (r.endTime) bits.push(`结束: ${fmtTime(r.endTime)}`);
  }
  if (p.maxBattle) {
    const kind = p.gmax ? "G-Max" : "D-Max";
    const cls = p.gmax ? "gmax-badge" : "dmax-badge";
    bits.push(`<span class="raid-badge ${cls}">${kind}</span> ${p.maxBattle.bossName || "进行中"}`);
  }
  if (p.entity === "EVENT") {
    bits.push(`<span class="raid-badge event-badge">${p.superMega ? "Super Mega" : "Event"}</span>`);
    if (p.address) bits.push(p.address);
    if (p.eventTime) bits.push(`开始: ${fmtTime(p.eventTime)}`);
    if (p.eventEndTime) bits.push(`结束: ${fmtTime(p.eventEndTime)}`);
  }
  if (p.entity === "ROUTE") {
    const km = p.distanceMeters != null ? (p.distanceMeters / 1000).toFixed(2) : "?";
    const mins = p.durationSeconds != null ? Math.round(p.durationSeconds / 60) : "?";
    bits.push(`<span class="raid-badge route-badge">ROUTE</span> ${km} km · ${mins} min`);
    if (p.reversible) bits.push("可双向");
  }
  bits.push(`距离: ${p.distanceM} m`);
  bits.push(`${p.lat.toFixed(6)}, ${p.lng.toFixed(6)}`);
  return bits.join("<br>");
}

function renderResult(data, { fitView = true } = {}) {
  lastResult = data;
  overlays.clearLayers();
  const { lat, lng, radius_m: radius, bbox, pois, counts, region } = data;
  $("n-stop").textContent = counts.POKESTOP ?? 0;
  $("n-gym").textContent = counts.GYM ?? 0;
  $("n-raid").textContent = counts.RAID ?? 0;
  $("n-mega").textContent = counts.SUPER_MEGA ?? 0;
  $("n-event").textContent = counts.EVENT ?? 0;
  $("n-gmax").textContent = counts.GMAX ?? 0;
  $("n-dmax").textContent = counts.DMAX ?? 0;
  $("n-power").textContent = counts.POWERSPOT ?? 0;
  $("n-route").textContent = counts.ROUTE ?? 0;
  const visible = pois.filter(visibleFilter);
  $("n-total").textContent = visible.length;

  const sw = bbox.sw;
  const ne = bbox.ne;
  L.rectangle(
    [
      [sw[0], sw[1]],
      [ne[0], ne[1]],
    ],
    { color: "#00c8c8", weight: 2, fillColor: "#00c8c8", fillOpacity: 0.06 }
  ).addTo(overlays);

  if (region === "circle") {
    L.circle([lat, lng], {
      radius,
      color: "#66bb6a",
      weight: 2,
      dashArray: "6",
      fill: false,
    }).addTo(overlays);
  }

  L.circleMarker([lat, lng], {
    radius: 8,
    color: "#2e7d32",
    fillColor: "#66bb6a",
    fillOpacity: 1,
    weight: 2,
  })
    .bindPopup("查询中心")
    .addTo(overlays);

  for (const p of visible) {
    if (p.entity === "ROUTE" && Array.isArray(p.path) && p.path.length >= 2) {
      L.polyline(p.path, {
        color: "#42a5f5",
        weight: 3,
        opacity: 0.85,
      }).addTo(overlays);
    }
    const icon = ICONS[p.entity];
    const z =
      p.gmax || p.superMega ? 500 : p.raid || p.dmax ? 400 : p.entity === "ROUTE" ? 200 : 0;
    const marker = icon
      ? L.marker([p.lat, p.lng], { icon, keyboard: false, zIndexOffset: z })
      : L.circleMarker([p.lat, p.lng], {
          radius: p.entity === "EVENT" ? 9 : 7,
          color: p.entity === "EVENT" ? "#ab47bc" : p.entity === "ROUTE" ? "#42a5f5" : "#888",
          fillColor: p.entity === "EVENT" ? "#ce93d8" : p.entity === "ROUTE" ? "#90caf9" : "#888",
          fillOpacity: 0.95,
          weight: 2,
        });
    if (p.raid) {
      L.circleMarker([p.lat, p.lng], {
        radius: 16,
        color: p.superMega ? "#7e57c2" : "#ff1744",
        weight: 2,
        fill: false,
      }).addTo(overlays);
    } else if (p.superMega) {
      L.circleMarker([p.lat, p.lng], {
        radius: 16,
        color: "#7e57c2",
        weight: 2,
        fill: false,
      }).addTo(overlays);
    }
    if (p.gmax || p.dmax) {
      L.circleMarker([p.lat, p.lng], {
        radius: 15,
        color: p.gmax ? "#ec407a" : "#ff9100",
        weight: 2,
        fill: false,
      }).addTo(overlays);
    }
    marker.bindPopup(popupHtml(p)).addTo(overlays);
  }

  if (fitView) map.setView([lat, lng], zoomForRadius(radius));
  setHint(
    `官方地图 ${visible.length} / ${pois.length} · Raid ${counts.RAID || 0} · Super Mega ${counts.SUPER_MEGA || 0} · G-Max ${counts.GMAX || 0} · D-Max ${counts.DMAX || 0} · Route ${counts.ROUTE || 0}`
  );
}

async function call(name, ...args) {
  const a = api();
  if (!a) throw new Error("桌面桥未就绪，请稍候再试");
  return a[name](...args);
}

function selectedDropTypes() {
  const types = [];
  if ($("f-stop").checked) types.push("PGO_POKESTOP");
  if ($("f-gym").checked || $("f-raid").checked || $("f-mega").checked) types.push("PGO_GYM");
  if ($("f-power").checked || $("f-gmax").checked || $("f-dmax").checked) types.push("PGO_POWERSPOT");
  if ($("f-route").checked) types.push("PGO_ROUTE");
  if ($("f-event").checked) types.push("CA_EVENT");
  return types;
}

async function search() {
  setBusy(true, "正在请求官方地图…");
  try {
    const res = await call(
      "search",
      $("coords").value,
      $("radius").value,
      $("region").value,
      selectedDropTypes()
    );
    if (!res.ok) throw new Error(res.error || "查询失败");
    renderResult(res);
  } catch (err) {
    setHint(String(err.message || err));
    alert(String(err.message || err));
  } finally {
    setBusy(false);
  }
}

["f-stop", "f-gym", "f-raid", "f-mega", "f-event", "f-gmax", "f-dmax", "f-power", "f-route"].forEach((id) => {
  $(id).addEventListener("change", () => {
    if (lastResult) renderResult(lastResult, { fitView: false });
  });
});

$("btn-search").addEventListener("click", search);
$("coords").addEventListener("keydown", (e) => {
  if (e.key === "Enter") search();
});
$("radius").addEventListener("keydown", (e) => {
  if (e.key === "Enter") search();
});
