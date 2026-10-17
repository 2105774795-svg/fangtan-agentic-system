import { useEffect, useRef } from 'react';

/** Canvas destination-out reveal, independently implemented after studying the
 * MiMoCodeWeb interaction. No code or visual assets from that repository copied.
 */
export function RevealBackdrop({ revealed }: { revealed: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    const surface = canvas?.parentElement;
    const context = canvas?.getContext('2d');
    if (!canvas || !surface || !context) return;
    const media = matchMedia('(prefers-reduced-motion: reduce)');
    let points: { x: number; y: number; time: number }[] = [];
    let previous: { x: number; y: number } | null = null;
    let frame = 0;
    let width = 0;
    let height = 0;
    const scale = Math.min(devicePixelRatio || 1, 2);
    const fill = () => {
      context.globalCompositeOperation = 'source-over';
      context.fillStyle = getComputedStyle(surface).getPropertyValue('--page').trim() || '#fcfaf8';
      context.fillRect(0, 0, width, height);
    };
    const resize = () => {
      width = surface.clientWidth;
      height = surface.clientHeight;
      canvas.width = Math.round(width * scale);
      canvas.height = Math.round(height * scale);
      context.setTransform(scale, 0, 0, scale, 0, 0);
      points = [];
      previous = null;
      fill();
    };
    const paint = (now: number) => {
      fill();
      context.globalCompositeOperation = 'destination-out';
      points = points.filter(point => now - point.time < 1450);
      for (const point of points) {
        const age = (now - point.time) / 1450;
        const radius = 36 + 138 * Math.sin(Math.min(age * 2.7, 1) * Math.PI / 2);
        const opacity = Math.min(1, (1 - age) * 2.8);
        const brush = context.createRadialGradient(point.x, point.y, 0, point.x, point.y, radius);
        brush.addColorStop(0, `rgba(0,0,0,${opacity})`);
        brush.addColorStop(.45, `rgba(0,0,0,${opacity * .85})`);
        brush.addColorStop(1, 'rgba(0,0,0,0)');
        context.fillStyle = brush;
        context.fillRect(point.x - radius, point.y - radius, radius * 2, radius * 2);
      }
      frame = points.length ? requestAnimationFrame(paint) : 0;
    };
    const move = (event: PointerEvent) => {
      if (revealed || media.matches || event.target instanceof Element && event.target.closest('textarea, button, input, a')) return;
      if (event.pointerType === 'touch' && !event.buttons) return;
      const rect = surface.getBoundingClientRect();
      const current = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      const start = previous || current;
      const count = Math.min(35, Math.max(1, Math.ceil(Math.hypot(current.x - start.x, current.y - start.y) / 20)));
      for (let i = 1; i <= count; i++) points.push({ x: start.x + (current.x - start.x) * i / count, y: start.y + (current.y - start.y) * i / count, time: performance.now() });
      if (points.length > 100) points.splice(0, points.length - 100);
      previous = current;
      if (!frame) frame = requestAnimationFrame(paint);
    };
    const leave = () => { previous = null; };
    const updateMedia = () => { canvas.style.visibility = revealed || media.matches ? 'hidden' : 'visible'; resize(); };
    const observer = new ResizeObserver(resize);
    observer.observe(surface);
    surface.addEventListener('pointermove', move);
    surface.addEventListener('pointerdown', move);
    surface.addEventListener('pointerleave', leave);
    media.addEventListener('change', updateMedia);
    updateMedia();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      surface.removeEventListener('pointermove', move);
      surface.removeEventListener('pointerdown', move);
      surface.removeEventListener('pointerleave', leave);
      media.removeEventListener('change', updateMedia);
    };
  }, [revealed]);
  return <><div className="relief-image" aria-hidden="true"/><canvas ref={canvasRef} className="reveal-mask" aria-hidden="true" data-testid="reveal-mask" /></>;
}
