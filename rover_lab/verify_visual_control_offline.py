"""Offline-only pixel instrument proposes premature pickup to test corrections.

No provider calls. Not an autonomous AI acceptance test or production detector.
"""
import argparse
import base64
import io
import json
from pathlib import Path
from PIL import Image
from agent.claude import Decision
from agent.engine import EngineClient
from agent.runner import run_command
from rover_lab.live import LiveRun
from rover_lab.verify_navigation_offline import sphere_center


def verify(port, output):
    output=Path(output)
    engine=EngineClient(f'http://127.0.0.1:{port}')
    operator=LiveRun(engine.base_url, output)
    operator.operator('/run/reset',{})
    operator.operator('/run/start',{'command':'Offline visual controller pickup check'})
    class PickupOnly:
        def state(self):
            state=engine.state()
            # Test ends on pickup; does not claim engine completion/delivery.
            return {**state, 'state':'Done'} if state['holding'] else state
        look=engine.look
        depth=engine.depth
        execute=engine.execute
    class PixelInstrument:
        def decide(self,messages,*,system):
            image=Image.open(io.BytesIO(base64.b64decode(messages[0]['content'][1]['source']['data'])))
            box=sphere_center(image,bounds=True)
            return Decision('pickup',{}, {}, 'Offline instrument only',
                {'box':box, 'path_clear':True, 'floor_zone':False})
    try:
        result=run_command('Offline pickup check',engine=PickupOnly(),claude=PixelInstrument(),
                           output_root=output,max_decisions=30)
        rows=[json.loads(line) for line in (result.run_dir/'steps.jsonl').read_text().splitlines()]
        assert engine.state()['holding'], rows[-3:]
        assert not any(row['result'].get('collided') for row in rows)
        pickups=[row for row in rows if row['tool']=='pickup']
        assert len(pickups)==1 and pickups[0]['result']['success'], pickups
        report={'execution_mode':'offline_pixel_instrument','provider_calls':0,
                'decisions':len(rows),'pickup_attempts':len(pickups),'collisions':0,
                'pickup':'pass','delivery':'not_tested',
                'corrections':sum(bool(row.get('controller_correction')) for row in rows)}
        (output/'report.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2))
    finally:
        operator.operator('/run/finish',{'reason':'abandoned'})
        operator.operator('/run/reset',{})

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--engine-port',type=int,required=True)
    p.add_argument('--output',required=True)
    a=p.parse_args(); verify(a.engine_port,a.output)
