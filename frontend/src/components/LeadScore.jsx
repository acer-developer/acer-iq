import React from "react";

const CONFIG = {
  "Hot Lead":     { bg: "bg-red-50",    text: "text-red-600",    dot: "bg-red-500",    border: "border-red-200" },
  "Warm Lead":    { bg: "bg-orange-50", text: "text-orange-600", dot: "bg-orange-500", border: "border-orange-200" },
  "Potential":    { bg: "bg-amber-50",  text: "text-amber-600",  dot: "bg-amber-400",  border: "border-amber-200" },
  "Low Priority": { bg: "bg-gray-100",  text: "text-gray-500",   dot: "bg-gray-400",   border: "border-gray-200" },
  "Pending":      { bg: "bg-gray-100",  text: "text-gray-400",   dot: "bg-gray-300",   border: "border-gray-200" },
};

export default function LeadScore({ label, score, size = "sm" }) {
  const c = CONFIG[label] ?? CONFIG["Pending"];
  const isLarge = size === "lg";

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-medium
        ${c.bg} ${c.text} ${c.border}
        ${isLarge ? "text-sm" : "text-xs"}`}
    >
      <span className={`rounded-full ${c.dot} ${isLarge ? "h-2 w-2" : "h-1.5 w-1.5"}`} />
      {isLarge ? `${label} · ${score}` : label}
    </span>
  );
}
