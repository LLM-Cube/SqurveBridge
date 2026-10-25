import { useLayoutEffect, useRef, useState } from 'react'

/**
 * A container that swaps between content of different heights (tabs) should not shrink back:
 * a shorter tab would clamp the scroll position and pull everything below it up. This keeps the
 * tallest height seen as a minimum, so only the first visit to a taller tab ever grows the area.
 */
export function useStableHeight() {
  const ref = useRef(null)
  const [tallest, setTallest] = useState(0)
  useLayoutEffect(() => {
    const height = ref.current?.offsetHeight || 0
    if (height > tallest) setTallest(height)
  })
  return [ref, tallest ? { minHeight: tallest } : undefined]
}
