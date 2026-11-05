"""Intentional wall-contact rehearsal of recovery priority; no model calls."""
import argparse
import json
from pathlib import Path
from unittest.mock import patch
from agent.claude import Decision
from agent.engine import EngineClient
from agent.runner import run_command
from rover_lab.live import LiveRun


def verify(port, output):
    engine=EngineClient(f'http://127.0.0.1:{port}')
    operator=LiveRun(engine.base_url,output)
    view={'box':[210,280,390,440],'path_clear':True,'floor_zone':False}
    actions=iter([Decision('move',{'distance':-2},{},'',view),
                  Decision('pickup',{}, {},'',view),
                  Decision('move',{'distance':-.2},{},'',view)])
    class Policy:
        def decide(self,*args,**kwargs): return next(actions)
    operator.operator('/run/reset',{})
    operator.operator('/run/start',{'command':'Offline intentional collision recovery check'})
    try:
        with patch('agent.claude.request',side_effect=AssertionError('Provider calls forbidden')), \
             patch('agent.openai_client.request',side_effect=AssertionError('Provider calls forbidden')):
            result=run_command('Offline recovery check',engine=engine,claude=Policy(),
                               output_root=output,max_decisions=3)
        rows=[json.loads(line) for line in (result.run_dir/'steps.jsonl').read_text().splitlines()]
        assert rows[0]['result'].get('collided'),rows
        assert rows[1]['tool']=='pickup' and not rows[1]['result']['success'],rows
        assert rows[2]['tool']=='move' and rows[2]['args']['distance']==.15,rows
        assert rows[2]['result']['success'] and not rows[2]['result']['collided'],rows
        assert engine.state()['state']!='Blocked'
        assert all(row['result'].get('outcome')!='not_dispatched' for row in rows)
        report={'execution_mode':'offline_intentional_collision','provider_calls':0,
                'intentional_contacts':1,'recovery':'pass','recovery_contacts':0,'navigation_stall':False}
        Path(output,'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
    finally:
        operator.operator('/run/finish',{'reason':'abandoned'})
        operator.operator('/run/reset',{})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--engine-port',type=int,required=True)
    p.add_argument('--output',required=True);a=p.parse_args();verify(a.engine_port,a.output)
