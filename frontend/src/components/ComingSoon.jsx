import React from "react";

export default function ComingSoon({ title, description, icon }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center bg-gray-50 py-20 text-center px-6">
      <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-blue-50">
        {icon || (
          <svg className="h-8 w-8 text-blue-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round"
              d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        )}
      </div>
      <span className="mb-2 inline-flex rounded-full bg-amber-100 px-3 py-1 text-xs font-bold text-amber-700 border border-amber-200">
        Coming Soon
      </span>
      <p className="text-lg font-semibold text-gray-900">{title}</p>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-gray-500">{description}</p>
    </div>
  );
}
