import {
    Area,
    AreaChart,
    CartesianGrid,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";
import { num, money } from "../lib/format";
import type { DailyStat } from "../api/client";

interface TimeSeriesChartProps {
    data: DailyStat[];
    metric: "impressions" | "clicks" | "conversions" | "spend" | "earnings";
    color?: string;
    height?: number;
}

export default function TimeSeriesChart({ data, metric, color = "#0066cc", height = 300 }: TimeSeriesChartProps) {
    if (!data || data.length === 0) {
        return (
            <div style={{ height, display: "flex", alignItems: "center", justifyContent: "center", background: "var(--bg-alt)", borderRadius: "8px" }}>
                <p className="muted">No data available for the selected period</p>
            </div>
        );
    }

    const formatYAxis = (value: any) => {
        if (metric === "spend" || metric === "earnings") return money(Number(value));
        return num(Number(value));
    };

    const formatTooltip = (value: any) => {
        if (metric === "spend" || metric === "earnings") return [money(Number(value)), metric];
        return [num(Number(value)), metric];
    };

    return (
        <div style={{ height, width: "100%", marginTop: "16px" }}>
            <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={data} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                    <defs>
                        <linearGradient id={`color-${metric}`} x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor={color} stopOpacity={0.3} />
                            <stop offset="95%" stopColor={color} stopOpacity={0} />
                        </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" />
                    <XAxis 
                        dataKey="stat_date" 
                        stroke="var(--muted)" 
                        fontSize={12} 
                        tickMargin={10} 
                        tickFormatter={(val) => {
                            const date = new Date(val);
                            return `${date.getMonth() + 1}/${date.getDate()}`;
                        }}
                    />
                    <YAxis 
                        stroke="var(--muted)" 
                        fontSize={12} 
                        tickFormatter={formatYAxis} 
                        width={60} 
                    />
                    <Tooltip 
                        formatter={formatTooltip}
                        labelFormatter={(label) => new Date(label as string).toLocaleDateString()}
                        contentStyle={{ borderRadius: "8px", border: "1px solid var(--border)", backgroundColor: "var(--bg)", color: "var(--fg)" }}
                    />
                    <Area 
                        type="monotone" 
                        dataKey={metric} 
                        stroke={color} 
                        strokeWidth={2}
                        fillOpacity={1} 
                        fill={`url(#color-${metric})`} 
                    />
                </AreaChart>
            </ResponsiveContainer>
        </div>
    );
}
