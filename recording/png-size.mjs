const MIN_WIDTH = 960;
const MIN_HEIGHT = 720;

export function pngSize(header, name) {
  if (header.length < 33 || header.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a' ||
      header.toString('ascii', 12, 16) !== 'IHDR') {
    throw new Error('Invalid PNG: ' + name);
  }
  const width = header.readUInt32BE(16);
  const height = header.readUInt32BE(20);
  if (width < MIN_WIDTH || height < MIN_HEIGHT) {
    throw new Error('Expected native dimensions of at least 960×720: ' + name);
  }
  return { width, height };
}
