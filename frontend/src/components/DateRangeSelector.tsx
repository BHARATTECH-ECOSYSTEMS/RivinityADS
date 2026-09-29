import { useState } from "react";

export type DateRange = "today" | "7d" | "30d" | "90d" | "custom";

export interface DateRangeState {
    preset: DateRange;
    startDate: string; // YYYY-MM-DD
    endDate: string; // YYYY-MM-DD
}

interface DateRangeSelectorProps {
    value: DateRangeState;
    onChange: (value: DateRangeState) => void;
}

function getPresetDates(preset: DateRange): { startDate: string; endDate: string } {
    const end = new Date();
    const start = new Date();
    if (preset === "7d") start.setDate(end.getDate() - 7);
    else if (preset === "30d") start.setDate(end.getDate() - 30);
    else if (preset === "90d") start.setDate(end.getDate() - 90);
    // for 'today', both are same
    
    return {
        startDate: start.toISOString().split("T")[0],
        endDate: end.toISOString().split("T")[0],
    };
}

export default function DateRangeSelector({ value, onChange }: DateRangeSelectorProps) {
    const handlePresetChange = (preset: DateRange) => {
        if (preset === "custom") {
            onChange({ ...value, preset });
        } else {
            const { startDate, endDate } = getPresetDates(preset);
            onChange({ preset, startDate, endDate });
        }
    };

    return (
        <div className="flex row gap align-center" style={{ marginBottom: "16px" }}>
            <select 
                className="input" 
                value={value.preset} 
                onChange={(e) => handlePresetChange(e.target.value as DateRange)}
                style={{ width: "auto" }}
            >
                <option value="today">Today</option>
                <option value="7d">Last 7 days</option>
                <option value="30d">Last 30 days</option>
                <option value="90d">Last 90 days</option>
                <option value="custom">Custom Range</option>
            </select>
            
            {value.preset === "custom" && (
                <div className="flex row gap align-center">
                    <input 
                        type="date" 
                        className="input" 
                        value={value.startDate} 
                        max={value.endDate}
                        onChange={(e) => onChange({ ...value, startDate: e.target.value })} 
                    />
                    <span>to</span>
                    <input 
                        type="date" 
                        className="input" 
                        value={value.endDate} 
                        min={value.startDate}
                        max={new Date().toISOString().split("T")[0]}
                        onChange={(e) => onChange({ ...value, endDate: e.target.value })} 
                    />
                </div>
            )}
        </div>
    );
}
