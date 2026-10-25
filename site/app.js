// The Orderline page: one JSON file from Databricks, rendered without a framework.

const PORTFOLIO_URL = "https://jagadish-butukuri.vercel.app";
const $ = (id) => document.getElementById(id);

const pct = (v, digits = 0) => (v === null || v === undefined ? "–" : `${(v * 100).toFixed(digits)}%`);
const int = (v) => (v === null || v === undefined ? "–" : Math.round(v).toLocaleString());
const stamp = (iso) =>
  iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }) : "unknown";
const day = (iso) => {
  const [y, m, d] = String(iso).slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString([], { month: "short", day: "numeric" });
};
const syncTime = (run) =>
  stamp(`${run.slice(0, 4)}-${run.slice(4, 6)}-${run.slice(6, 8)}T${run.slice(9, 11)}:${run.slice(11, 13)}:${run.slice(13, 15)}Z`);

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children.filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}

function bar(share, max = 1, route = false) {
  const width = Math.max(0, Math.min(100, (share / max) * 100));
  const outer = el("div", { className: route ? "bar route" : "bar" });
  outer.append(el("span", { style: `width:${width}%` }));
  return outer;
}

function table(target, headers, rows) {
  const head = el("tr", {}, ...headers.map(([label, cls]) => el("th", { className: cls || "", textContent: label })));
  $(target).replaceChildren(el("thead", {}, head), el("tbody", {}, ...rows));
}

function cell(content, cls = "") {
  const td = el("td", { className: cls });
  td.append(content instanceof Node ? content : document.createTextNode(content));
  return td;
}

// ---------------------------------------------------------------- header

function header(data) {
  const s = data.summary;
  $("fresh").textContent =
    `Last updated ${stamp(data.generated_at)} · shop changes through ${stamp(s.last_commit)} · ` +
    `scored ${stamp(s.scored_at)}`;
  const run = data.runs.find((r) => r.promoted) || data.runs[0];
  const tiles = [
    ["Orders captured", int(s.orders), `${int(s.customers)} customers`],
    ["Open right now", int(s.open_orders), "paid, packed or shipped"],
    ["Late, last 30 days", pct(s.late_share_30d, 1), `${s.lead_days_30d?.toFixed(1) ?? "–"} days door to door`],
    run
      ? ["Model AUC", run.auc.toFixed(3), `vs ${run.baseline_auc.toFixed(3)} from carrier late rates`]
      : ["Model", "training", "first run pending"],
  ];
  $("tiles").replaceChildren(
    ...tiles.map(([k, v, d]) => el("div", { className: "tile" }, el("div", { className: "k", textContent: k }),
      el("div", { className: "v num", textContent: v }), el("div", { className: "d", textContent: d }))),
  );
}

// ---------------------------------------------------------------- daily chart

// Days whose orders mostly haven't reached their promise date yet. Their late share
// would read low only because the late ones haven't had time to be late.
const STILL_ARRIVING_DAYS = 6;

function dailyChart(rows) {
  const narrow = $("daily").clientWidth < 600;
  const W = narrow ? 560 : 1000, H = narrow ? 340 : 300, pad = { l: 40, r: 44, t: 10, b: 30 };
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const nice = (v, step) => Math.ceil(v / step) * step;
  const maxOrders = nice(Math.max(...rows.map((r) => r.orders)) * 1.05, 100);
  const settledFrom = rows.length - STILL_ARRIVING_DAYS;
  const shares = rows.map((r, i) => (r.delivered && i < settledFrom ? r.late / r.delivered : null));
  const maxShare = nice(Math.max(0.2, ...shares.filter((v) => v !== null)) * 1.05, 0.1);
  const step = iw / rows.length;
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Daily orders and late share over the last 60 days");
  const add = (tag, attrs) => {
    const node = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    svg.append(node);
    return node;
  };
  const css = getComputedStyle(document.documentElement);
  const color = (name) => css.getPropertyValue(name).trim();

  for (const frac of [0, 0.25, 0.5, 0.75, 1]) {
    const y = pad.t + ih * (1 - frac);
    add("line", { x1: pad.l, x2: W - pad.r, y1: y, y2: y, stroke: color("--line"), "stroke-width": 1 });
    const left = add("text", { x: pad.l - 8, y: y + 4, "text-anchor": "end", "font-size": 12, fill: color("--muted") });
    left.textContent = Math.round(maxOrders * frac);
    const right = add("text", { x: W - pad.r + 8, y: y + 4, "font-size": 12, fill: color("--late") });
    right.textContent = `${Math.round(maxShare * frac * 100)}%`;
  }
  const shadeX = pad.l + settledFrom * step;
  add("rect", { x: shadeX, y: pad.t, width: W - pad.r - shadeX, height: ih, fill: color("--line"), opacity: 0.35 });
  const shadeLabel = add("text", { x: W - pad.r - 6, y: pad.t + 16, "text-anchor": "end", "font-size": 12, fill: color("--muted") });
  shadeLabel.textContent = "still arriving";
  rows.forEach((r, i) => {
    const h = (r.orders / maxOrders) * ih;
    const rect = add("rect", {
      x: pad.l + i * step + step * 0.15, y: pad.t + ih - h, width: step * 0.7, height: h, rx: 2,
      fill: color("--route-soft"), stroke: color("--route"), "stroke-width": 0.6,
    });
    rect.dataset.tip = `${day(r.day)}: ${int(r.orders)} orders, $${int(r.revenue_usd)}, ` +
      (shares[i] !== null ? `${pct(shares[i], 1)} late of ${int(r.delivered)} delivered` : "still arriving");
    if (i % Math.ceil(rows.length / (narrow ? 4 : 8)) === 0) {
      const label = add("text", { x: pad.l + i * step + step / 2, y: H - 8, "text-anchor": "middle", "font-size": 12, fill: color("--muted") });
      label.textContent = day(r.day);
    }
  });
  const point = (i) => `${pad.l + i * step + step / 2},${pad.t + ih * (1 - shares[i] / maxShare)}`;
  const line = shares.map((v, i) => (v !== null ? point(i) : null)).filter(Boolean);
  add("polyline", { points: line.join(" "), fill: "none", stroke: color("--late"), "stroke-width": 2.5, "stroke-linejoin": "round" });

  svg.addEventListener("mousemove", (e) => {
    const tip = $("tip");
    if (!e.target.dataset?.tip) return tip.classList.add("hidden");
    tip.textContent = e.target.dataset.tip;
    tip.style.left = `${e.clientX + 12}px`;
    tip.style.top = `${e.clientY + 12}px`;
    tip.classList.remove("hidden");
  });
  svg.addEventListener("mouseleave", () => $("tip").classList.add("hidden"));
  $("daily").replaceChildren(svg);
}

// ---------------------------------------------------------------- causes

function causes(data) {
  const worst = Math.max(...data.carriers.map((c) => c.late_share ?? 0), 0.01);
  table("carriers", [["Carrier"], ["Method"], ["Late", "r"], [""], ["Days", "r hide-sm"]],
    data.carriers.map((c) => el("tr", {}, cell(c.carrier), cell(c.ship_method), cell(pct(c.late_share), "r num"),
      cell(bar(c.late_share ?? 0, worst)), cell(c.lead_days?.toFixed(1) ?? "–", "r num hide-sm"))));

  table("warehouses", [["Warehouse"], ["Avg load", "r"], ["Peak", "r hide-sm"], ["Overflow", "r"], ["Late", "r"]],
    data.warehouses.map((w) => el("tr", {}, cell(w.warehouse), cell(`${w.avg_load.toFixed(2)}×`, "r num"),
      cell(`${w.peak_load.toFixed(2)}×`, "r num hide-sm"), cell(pct(w.overflow_share), "r num"), cell(pct(w.late_share), "r num"))));
}

// ---------------------------------------------------------------- model

function model(data) {
  const run = data.runs.find((r) => r.promoted) || data.runs[0];
  if (!run) {
    $("checks").replaceChildren(el("li", { textContent: "The first training run hasn't finished yet." }));
    return;
  }
  $("model-sub").textContent =
    `Champion: version ${run.model_version}, trained ${stamp(run.trained_at)} and tested on ${int(run.test_orders)} orders ` +
    `placed since ${day(run.test_from)} (${pct(run.late_share)} late). AUC ${run.auc.toFixed(3)}, average precision ` +
    `${run.average_precision.toFixed(3)}. Knowing only each carrier and method's late rate gets ${run.baseline_auc.toFixed(3)}.`;

  const checks = Object.entries(run.checks);
  $("checks").replaceChildren(...checks.map(([name, detail]) =>
    el("li", {}, el("span", { className: run.checks_passed ? "mark" : "mark bad", textContent: run.checks_passed ? "✓" : "?" }),
      el("div", {}, document.createTextNode(name), el("small", { textContent: detail })))));

  const top = Object.entries(run.importances).slice(0, 7);
  const most = Math.max(...top.map(([, v]) => v), 0.001);
  const names = {
    distance_km: "distance", weight_g: "weight", carrier: "carrier", ship_method: "standard or express",
    placed_dow: "day of week", placed_hour: "hour of day", warehouse_load: "warehouse backlog",
    warehouse: "warehouse", customer_segment: "customer segment", item_count: "items", total_usd: "order value",
    from_nearest_warehouse: "nearest warehouse",
  };
  const label = (name) => names[name] ?? name.replaceAll("_", " ");
  $("importances").replaceChildren(...top.map(([name, v]) =>
    el("div", { className: "imp" }, el("span", { textContent: label(name) }), bar(Math.max(v, 0), most, true),
      el("span", { className: "num", textContent: v.toFixed(3) }))));

  const order = { high: 0, medium: 1, low: 2 };
  const record = [...data.track_record].sort((a, b) => order[a.risk_band] - order[b.risk_band]);
  if (!record.length) {
    $("track").replaceChildren(el("p", { className: "note", textContent:
      "Builds up as promise dates pass: an order scored today is judged once its promise date is behind it." }));
  } else {
    const t = el("table");
    $("track").replaceChildren(t);
    t.id = "track-table";
    table("track-table", [["Risk band"], ["Orders", "r"], ["Predicted", "r"], ["Actually late", "r"]],
      record.map((r) => el("tr", {}, cell(el("span", { className: `pill ${r.risk_band}`, textContent: r.risk_band })),
        cell(int(r.orders), "r num"), cell(pct(r.predicted, 1), "r num"), cell(pct(r.actual, 1), "r num"))));
  }
}

// ---------------------------------------------------------------- risky and syncs

function risky(data) {
  const bands = Object.fromEntries(data.bands.map((b) => [b.risk_band, b.orders]));
  $("risky-sub").textContent =
    `Scored by the champion after the latest sync: ${int(bands.high ?? 0)} high, ${int(bands.medium ?? 0)} medium and ` +
    `${int(bands.low ?? 0)} low risk. Look at why the top ones are up there: far, heavy, Parcelo, or a busy warehouse.`;
  table("risky", [["Order"], ["Risk", "r"], [""], ["Carrier"], ["From"], ["Km", "r"], ["Kg", "r hide-sm"], ["Load", "r hide-sm"], ["Promised", "hide-sm"], ["Status", "hide-sm"]],
    data.risky.map((r) => el("tr", {}, cell(`#${r.order_id}`, "num"), cell(pct(r.p_late), "r num"),
      cell(el("span", { className: `pill ${r.risk_band}`, textContent: r.risk_band })),
      cell(`${r.carrier} ${r.ship_method === "express" ? "express" : ""}`.trim()),
      cell(`${r.warehouse} → ${r.customer_state}`), cell(int(r.distance_km), "r num"), cell(r.weight_kg.toFixed(1), "r num hide-sm"),
      cell(`${r.warehouse_load.toFixed(2)}×`, "r num hide-sm"), cell(day(r.promised_on), "hide-sm"), cell(r.status, "hide-sm"))));
}

function syncs(data) {
  table("syncs", [["Sync"], ["Events", "r"], ["Inserts", "r hide-sm"], ["Updates", "r hide-sm"], ["Deletes", "r hide-sm"], ["Minutes to land", "r"]],
    data.syncs.map((s) => el("tr", {}, cell(syncTime(s.sync_run)), cell(int(s.events), "r num"), cell(int(s.inserts), "r num hide-sm"),
      cell(int(s.updates), "r num hide-sm"), cell(int(s.deletes), "r num hide-sm"), cell(s.minutes_to_land?.toFixed(1) ?? "–", "r num"))));
}

// ---------------------------------------------------------------- go

(async () => {
  $("hub-link").href = PORTFOLIO_URL;
  $("hub-link").classList.remove("hidden");
  let data;
  try {
    const response = await fetch("data/site.json", { cache: "no-cache" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    data = await response.json();
  } catch (error) {
    $("fresh").textContent = "Couldn't load the numbers.";
    $("failed").textContent = `The data file didn't load (${error.message}). Try again in a minute.`;
    $("failed").classList.remove("hidden");
    return;
  }
  for (const [name, render] of [["header", header], ["daily", () => dailyChart(data.daily)], ["causes", causes],
    ["model", model], ["risky", risky], ["syncs", syncs]]) {
    try {
      render(data);
    } catch (error) {
      console.error(`rendering ${name} failed`, error);
    }
  }
})();
