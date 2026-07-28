import React from 'react'

export default function BrandLogo({ className = '', compact = false }) {
  return (
    <span
      aria-label="Garmin Running Dashboard"
      className={`brand_logo ${compact ? 'brand_logo_compact' : ''} ${className}`.trim()}
      data-name="brand_logo"
    >
      <img
        src="/brand-mark-v2.svg"
        alt=""
        className="brand_logo_mark"
        data-name="brand_logo_mark"
        aria-hidden="true"
      />
    </span>
  )
}
