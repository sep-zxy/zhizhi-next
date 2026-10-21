export interface RectLike {
  top: number;
  right: number;
  bottom: number;
  left: number;
  width: number;
  height: number;
}

export function calculateBottomOverlayOffset(view: RectLike, overlay: RectLike) {
  const overlapsHorizontally = overlay.right > view.left && overlay.left < view.right;
  const overlapsVertically = overlay.bottom > view.top && overlay.top < view.bottom;

  if (!overlapsHorizontally || !overlapsVertically || overlay.width <= 0 || overlay.height <= 0) {
    return 0;
  }

  return Math.ceil(Math.min(overlay.height, Math.max(0, view.bottom - overlay.top)));
}

