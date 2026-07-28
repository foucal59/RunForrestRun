import React from 'react'

export default function Loader() {
  return (
    <div className="flex items-center justify-center py-20 loader" data-name="loader">
      <div className="w-8 h-8 border-2 border-brand/30 border-t-brand rounded-full animate-spin loader_spinner" data-name="loader_spinner" />
    </div>
  )
}
