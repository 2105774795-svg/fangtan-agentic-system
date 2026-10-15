export function waveHeader(dataBytes: number): ArrayBuffer {
  const buffer = new ArrayBuffer(44);
  const view = new DataView(buffer);
  const text = (offset: number, value: string): void => {
    for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
  };
  text(0, 'RIFF'); view.setUint32(4, 36 + dataBytes, true); text(8, 'WAVE'); text(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true); view.setUint32(28, 32000, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, 'data'); view.setUint32(40, dataBytes, true);
  return buffer;
}

export function joinBytes(parts: Uint8Array[]): ArrayBuffer {
  let size = 0;
  parts.forEach(part => size += part.byteLength);
  const result = new Uint8Array(size);
  let offset = 0;
  parts.forEach(part => { result.set(part, offset); offset += part.byteLength; });
  return result.buffer;
}
