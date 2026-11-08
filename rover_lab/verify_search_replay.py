"""Replay saved AI proposals to regress the delivery-search deadlock offline.

After the corrected motion diverges, old perceptions are only test inputs, not
current visual truth. This tests controller liveness/contact, not live recognition.
"""
import argparse
import json
from pathlib import Path
from unittest.mock import patch
from agent.claude import Decision
from agent.engine import EngineClient
from agent.runner import run_command
from rover_lab.live import LiveRun


def verify(port, source, output):
    records=[json.loads(line) for line in Path(source).read_text().splitlines()]
    iterator=iter(records)
    engine=EngineClient(f'http://127.0.0.1:{port}')
    operator=LiveRun(engine.base_url, output)
    class Replay:
        def decide(self, messages, *, system):
            row=next(iterator)
            return Decision(row['proposal']['tool'],row['proposal']['args'],{},
                            row['visual_observation'],row['target_view'])
    operator.operator('/run/reset',{})
    operator.operator('/run/start',{'command':'Offline saved AI delivery-search replay'})
    try:
        with patch('agent.claude.request',side_effect=AssertionError('Paid calls forbidden')), \
             patch('agent.openai_client.request',side_effect=AssertionError('Paid calls forbidden')):
            result=run_command('Fetch the red sphere and drop it in the green zone',
                               engine=engine,claude=Replay(),output_root=output,max_decisions=len(records))
        rows=[json.loads(line) for line in (result.run_dir/'steps.jsonl').read_text().splitlines()]
        assert result.status=='decision_limit', result
        assert engine.state()['holding']
        assert len(rows)==len(records)
        assert not any(row['result'].get('collided') or row['result'].get('outcome')=='not_dispatched' for row in rows)
        reposition=[row for row in rows if row.get('controller_correction')=='reposition_after_full_zone_scan']
        assert reposition and all(row['result']['success'] for row in reposition)
        report={'execution_mode':'offline_saved_ai_search_replay','provider_calls':0,
                'decisions':len(rows),'pickup':'pass','repositions':[row['step'] for row in reposition],
                'navigation_stall':False,'contacts':0,'delivery':'not_verified'}
        Path(output,'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
    finally:
        operator.operator('/run/finish',{'reason':'abandoned'})
        operator.operator('/run/reset',{})

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--engine-port',type=int,required=True)
    p.add_argument('--source',required=True)
    p.add_argument('--output',required=True)
    a=p.parse_args();verify(a.engine_port,a.source,a.output)
