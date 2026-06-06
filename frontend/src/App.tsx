import { useEffect, useState } from 'react'

const OPS_API = import.meta.env.VITE_OPS_API_URL ?? 'http://localhost:8080'

interface MRASEvent {
  trigger_id: string
  ts: string
  service: string
  event_type: string
  status: string
  payload: Record<string, unknown>
}

export default function App() {
  const [events, setEvents] = useState<MRASEvent[]>([])

  useEffect(() => {
    const es = new EventSource(`${OPS_API}/events/stream`)
    es.onmessage = (e) => {
      const ev = JSON.parse(e.data) as MRASEvent
      setEvents(prev => [ev, ...prev].slice(0, 200))
    }
    return () => es.close()
  }, [])

  return (
    <div style={{ fontFamily: 'monospace', padding: 16, background: '#111', color: '#eee', minHeight: '100vh' }}>
      <h2 style={{ marginBottom: 12 }}>MRAS Activity Feed</h2>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr>
            {['time', 'service', 'type', 'status', 'trigger_id'].map(h => (
              <th key={h} style={{ textAlign: 'left', padding: '4px 8px', borderBottom: '1px solid #333' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {events.map((ev, i) => (
            <tr key={i} style={{ color: ev.status === 'error' ? '#f88' : '#eee' }}>
              <td style={{ padding: '2px 8px' }}>{new Date(ev.ts).toLocaleTimeString()}</td>
              <td style={{ padding: '2px 8px' }}>{ev.service}</td>
              <td style={{ padding: '2px 8px' }}>{ev.event_type}</td>
              <td style={{ padding: '2px 8px' }}>{ev.status}</td>
              <td style={{ padding: '2px 8px', color: '#888' }}>{ev.trigger_id?.slice(0, 8)}…</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
