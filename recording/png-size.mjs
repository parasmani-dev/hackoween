const MIN_WIDTH = 960;
const MIN_HEIGHT = 720;

export function pngSize(header, name, minWidth = MIN_WIDTH, minHeight = MIN_HEIGHT) {
  if (header.length < 33 || header.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a' ||
      header.toString('ascii', 12, 16) !== 'IHDR') {
    throw new Error('Invalid PNG: ' + name);
  }
  const width = header.readUInt32BE(16);
  const height = header.readUInt32BE(20);
  if (width < minWidth || height < minHeight) {
    throw new Error('Expected dimensions of at least ' + minWidth + '×' + minHeight + ': ' + name);
  }
  return { width, height };
}
