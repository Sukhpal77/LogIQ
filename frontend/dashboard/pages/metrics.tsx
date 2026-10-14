import { useState, useEffect } from "react";
import { useRouter } from "next/router";
import useSWR from "swr";
import { apiFetch } from "../lib/api";

const fetcher = (path: string) => apiFetch(path);

function Chart({ rows, field, label, ceiling }: { rows: any[]; field: string; label: string; ceiling: number }) {
  const width = 540, height = 140;
  const maximum = Math.max(ceiling, ...rows.map(row => row[field]));
  const points = rows.map((row, i) => `${10 + i * (width - 20) / Math.max(1, rows.length - 1)},${height - 10 - row[field] / maximum * (height - 20)}`).join(" ");
  const latest = rows.length ? rows[rows.length - 1][field] : null;
  return <section className="panel">
    <h3>{label}: {latest === null ? "No readings" : latest}</h3>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${label} recent readings, range 0 to ${maximum}`} style={{ width: "100%" }}>
      <line x1="10" x2={width - 10} y1={height - 10} y2={height - 10} stroke="#526078" />
      <polyline points={points} fill="none" stroke="#087f77" strokeWidth="2" />
      {rows.length === 1 && <circle cx="10" cy={height - 10 - latest / maximum * (height - 20)} r="3" fill="#087f77" />}
    </svg>
    <small>Last {rows.length} readings · range 0–{maximum}</small>
  </section>;
}

export default function Metrics() {
  const router=useRouter();
  const [minutes,setMinutes]=useState("60");
  const [service, setService] = useState("");
  useEffect(()=>{if(router.query.service)setService(String(router.query.service));},[router.query.service]);
  const services = useSWR("/context/services", fetcher);
  const metrics = useSWR(service ? `/workspace/metrics?service=${encodeURIComponent(service)}&minutes=${minutes}` : null, fetcher, { refreshInterval: 10000 });
  const context = useSWR(service ? `/context/services/${encodeURIComponent(service)}/neighborhood` : null, fetcher);
  const rows = metrics.data?.items || [];
  return <div>
    <div className="eyebrow">Performance signals</div><h1>Service performance</h1><p>Measured CPU, memory, and latency for your monitored services.</p>
    <label>Service <input list="services" placeholder="Enter your service name" value={service} onChange={event => setService(event.target.value)} /></label>
    <label style={{maxWidth:220,marginTop:15}}>Time range<select value={minutes} onChange={e=>setMinutes(e.target.value)}><option value="15">Last 15 minutes</option><option value="60">Last hour</option><option value="360">Last 6 hours</option><option value="1440">Last 24 hours</option></select></label><p>Charts show average readings per time bucket; missing data is not filled with zeros.</p><datalist id="services">{(services.data || []).map((item: any) => <option key={item.name} value={item.name} />)}</datalist>
    {metrics.error && <p role="alert">Unable to load readings: {metrics.error.message}</p>}
    {service && <div style={{ display: "grid", gap: 16, marginTop: 16 }}>
      <Chart rows={rows} field="cpu" label="CPU (%)" ceiling={100} />
      <Chart rows={rows} field="memory" label="Memory (%)" ceiling={100} />
      <Chart rows={rows} field="latency" label="Latency (ms)" ceiling={1000} />
      <section><h3>Dependencies</h3>
        <p>Depends on: {(context.data?.upstream || []).join(", ") || "None registered"}</p>
        <p>Used by: {(context.data?.downstream || []).join(", ") || "None registered"}</p>
      </section>
    </div>}
  </div>;
}
