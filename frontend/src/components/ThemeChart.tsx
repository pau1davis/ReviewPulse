import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ThemeWeek } from "@/lib/api";

interface Props {
  series: ThemeWeek[];
}

// Aggregate theme counts across all weeks, return top 10 sorted descending
function aggregate(series: ThemeWeek[]) {
  const totals: Record<string, number> = {};
  for (const { theme, count } of series) {
    totals[theme] = (totals[theme] ?? 0) + count;
  }
  return Object.entries(totals)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10)
    .map(([theme, count]) => ({ theme, count }));
}

// A palette of muted blues/purples for the bars
const PALETTE = [
  "#6366f1", "#8b5cf6", "#a855f7", "#3b82f6", "#0ea5e9",
  "#06b6d4", "#14b8a6", "#10b981", "#84cc16", "#f59e0b",
];

export default function ThemeChart({ series }: Props) {
  const data = aggregate(series);

  if (data.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">
        No theme data yet.
      </p>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={Math.max(200, data.length * 36)}>
      <BarChart
        layout="vertical"
        data={data}
        margin={{ top: 4, right: 24, left: 8, bottom: 4 }}
      >
        <CartesianGrid
          strokeDasharray="3 3"
          horizontal={false}
          stroke="hsl(214.3 31.8% 91.4%)"
        />
        <XAxis
          type="number"
          allowDecimals={false}
          tick={{ fontSize: 12 }}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          type="category"
          dataKey="theme"
          width={100}
          tick={{ fontSize: 12 }}
          tickLine={false}
          axisLine={false}
        />
        <Tooltip
          contentStyle={{
            borderRadius: "0.5rem",
            border: "1px solid hsl(214.3 31.8% 91.4%)",
            fontSize: 13,
          }}
        />
        <Bar dataKey="count" radius={[0, 4, 4, 0]}>
          {data.map((_, i) => (
            <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
