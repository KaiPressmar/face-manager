export const MAX_ALBUM_ARCHIVE_PAGES = 32;
export const MAX_ALBUM_ARCHIVE_BYTES = 64 * 1024 * 1024;
export const MAX_ALBUM_PAGE_BYTES = 40 * 1024 * 1024;

const crcTable = Uint32Array.from({ length: 256 }, (_, index) => {
  let crc = index;
  for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (crc & 1 ? 0xEDB88320 : 0);
  return crc;
});

function crc32(bytes: Uint8Array) {
  let crc = 0xFFFFFFFF;
  for (const byte of bytes) crc = (crc >>> 8) ^ crcTable[(crc ^ byte) & 255];
  return (crc ^ 0xFFFFFFFF) >>> 0;
}

/** PNGs already use compression. Store them in a ZIP without re-encoding. */
export async function archiveMapAlbumPages(pages: readonly Blob[]): Promise<Blob> {
  const entries: BlobPart[] = [];
  const directory: ArrayBuffer[] = [];
  let position = 0;
  let directorySize = 0;
  const encoder = new TextEncoder();
  for (const [index, png] of pages.entries()) {
    const name = encoder.encode(`Seite-${String(index + 1).padStart(2, "0")}.png`);
    const crc = crc32(new Uint8Array(await png.arrayBuffer()));

    const local = new ArrayBuffer(30);
    const localHeader = new DataView(local);
    localHeader.setUint32(0, 0x04034B50, true);
    localHeader.setUint16(4, 20, true);
    localHeader.setUint16(12, 33, true); // DOS 1980-01-01
    localHeader.setUint32(14, crc, true);
    localHeader.setUint32(18, png.size, true);
    localHeader.setUint32(22, png.size, true);
    localHeader.setUint16(26, name.length, true);
    entries.push(local, name, png);

    const central = new ArrayBuffer(46 + name.length);
    const centralHeader = new DataView(central);
    centralHeader.setUint32(0, 0x02014B50, true);
    centralHeader.setUint16(4, 20, true);
    centralHeader.setUint16(6, 20, true);
    centralHeader.setUint16(14, 33, true);
    centralHeader.setUint32(16, crc, true);
    centralHeader.setUint32(20, png.size, true);
    centralHeader.setUint32(24, png.size, true);
    centralHeader.setUint16(28, name.length, true);
    centralHeader.setUint32(42, position, true);
    new Uint8Array(central, 46).set(name);
    directory.push(central);
    directorySize += central.byteLength;
    position += local.byteLength + name.length + png.size;
  }

  const end = new ArrayBuffer(22);
  const finalHeader = new DataView(end);
  finalHeader.setUint32(0, 0x06054B50, true);
  finalHeader.setUint16(8, pages.length, true);
  finalHeader.setUint16(10, pages.length, true);
  finalHeader.setUint32(12, directorySize, true);
  finalHeader.setUint32(16, position, true);
  return new Blob([...entries, ...directory, end], { type: "application/zip" });
}
