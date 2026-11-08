"""Camera-calibration rehearsal on Scenario 1; no model calls or private poses.

The red-pixel component detector is an offline test instrument, not the production
agent. It checks the small-turn/short-approach/pickup guidance on real camera pixels.
"""
import argparse
import json
import math
from io import BytesIO
from pathlib import Path

from PIL import Image
from agent.engine import EngineClient
from rover_lab.live import LiveRun


def sphere_center(image, *, bounds=False):
    sample = image.resize((160, 120)).convert('RGB')
    pixels = sample.load()
    remaining = {(x, y) for y in range(35, 120) for x in range(160)
                 if pixels[x, y][0] > 70 and pixels[x, y][0] > 1.5 * pixels[x, y][1]
                 and pixels[x, y][0] > 1.5 * pixels[x, y][2]}
    components = []
    while remaining:
        start = remaining.pop()
        found, queue = [start], [start]
        while queue:
            x, y = queue.pop()
            for neighbor in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
                    found.append(neighbor)
        if len(found) > 25:
            xs, ys = zip(*found)
            width, height = max(xs)-min(xs)+1, max(ys)-min(ys)+1
            fill = len(found)/(width*height)
            # A sphere's projected silhouette is round; the red cube fills its rectangle.
            if (0.65 < width/height < 1.5 and fill < 0.87) or max(ys) >= 118:
                components.append((max(ys), sum(xs)/len(xs)*4, fill,
                                   [min(xs)*4, min(ys)*4, (max(xs)+1)*4, (max(ys)+1)*4]))
    if not components:
        raise AssertionError('Offline instrument could not identify a round red component')
    return max(components)[3 if bounds else 1]


def verify(engine_port, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    engine = EngineClient(f'http://127.0.0.1:{engine_port}')
    operator = LiveRun(engine.base_url, output)
    operator.operator('/run/reset', {})
    operator.operator('/run/start', {'command': 'Offline camera alignment and pickup check'})
    try:
        def capture(index):
            image = Image.open(BytesIO(engine.look())).convert('RGB')
            image.save(output / f'{index:04d}.png')
            return image
        before = sphere_center(capture(0))
        degrees = max(-25, min(25, math.degrees(math.atan((before-320)/320))))
        assert engine.execute('turn', {'degrees': degrees})['success']
        after = sphere_center(capture(1))
        assert abs(after-320) < abs(before-320), (before, after, degrees)
        assert abs(after-320) < 70, (before, after)
        # Trial pickup is a reach check; only short, bounded moves follow failures.
        moves = 0
        trace = []
        for index in range(24):
            if engine.execute('pickup', {})['success']:
                break
            current = capture(2+index*2)
            center = sphere_center(current)
            correction = max(-15, min(15, math.degrees(math.atan((center-320)/320))))
            if abs(correction) > 5:
                assert engine.execute('turn', {'degrees': correction})['success']
            # Partial clipping does not establish reach. Near contact, keep
            # increments below the narrow pickup-before-collision window.
            bottom = [current.getpixel((x, y)) for y in range(475, 480) for x in range(640)]
            clipped = sum(r > 70 and r > 1.5*g and r > 1.5*b for r,g,b in bottom) > 30
            distance = 0.05 if clipped else 0.2 if moves < 3 else 0.1
            depth = engine.depth()
            if depth is not None:
                distance = min(distance, max(0.0, depth - 0.04))
                assert distance > 0.001, 'At clearance limit but pickup failed; realign instead of forcing contact'
            result = engine.execute('move', {'distance': distance})
            trace.append({'center_x': center, 'correction': correction, 'depth': depth,
                          'move': distance, 'result': result})
            assert result['success'] and not result['collided'], result
            moves += 1
            capture(3+index*2)
        else:
            raise AssertionError('Short-approach rehearsal did not reach pickup')
        assert engine.state()['holding']
        report = {'execution_mode': 'offline_camera_calibration', 'provider_calls': 0,
                  'before_center_x': before, 'after_center_x': after, 'turn_degrees': degrees,
                  'short_approaches': moves, 'pickup': 'pass', 'collisions': 0,
                  'range_samples': sum(row['depth'] is not None for row in trace), 'trace': trace}
        (output / 'navigation-report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    finally:
        operator.operator('/run/finish', {'reason': 'abandoned'})
        operator.operator('/run/reset', {})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine-port', required=True, type=int)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    verify(args.engine_port, args.output)
