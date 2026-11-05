"""Real-Webots full fetch with deliberately poor mocked AI proposals.

The production camera controller must ground perception and route from live pixels.
No fixture waypoint or true object/zone coordinate is supplied to it.
"""
import argparse,json
from pathlib import Path
from unittest.mock import patch
from agent.claude import Decision
from agent.engine import EngineClient
from agent.runner import run_command
from rover_lab.live import LiveRun


def verify(port,output,command,initial_turn=0):
    engine=EngineClient(f'http://127.0.0.1:{port}');operator=LiveRun(engine.base_url,output)
    class PoorPolicy:
        def set_available_actions(self,names):self.names=names
        def decide(self,*args,**kwargs):
            name='drop' if 'drop' in self.names else 'pickup' if 'pickup' in self.names else 'turn'
            return Decision(name,{'degrees':45} if name=='turn' else {},{},'Offline deliberately inaccurate proposal',
                            {'box':[210,280,390,440],'path_clear':True,'floor_zone':True})
    operator.operator('/run/reset',{})
    data={'command':command,'record_pov':True}
    # Only the operator knows evaluator identity; not passed to agent or policy.
    expected={'Fetch the red sphere and drop it in the green zone':'item_1',
              'Fetch the blue cylinder and drop it in the green zone':'item_3',
              'Fetch the yellow cone and drop it in the green zone':'item_5'}
    if command in expected:data['expected_fixture_key']=expected[command]
    operator.operator('/run/start',data)
    try:
        if initial_turn:
            assert engine.execute('turn',{'degrees':initial_turn})['success']
        with patch('agent.claude.request',side_effect=AssertionError('Provider calls forbidden')), \
             patch('agent.openai_client.request',side_effect=AssertionError('Provider calls forbidden')):
            result=run_command(command,engine=engine,claude=PoorPolicy(),output_root=output,max_decisions=60)
        verdict=operator.operator('/run/finish',{'reason':'abandoned'})
        rows=[json.loads(x) for x in (result.run_dir/'steps.jsonl').read_text().splitlines()]
        report={'mode':'offline_camera_controller_with_mocked_ai','provider_calls':0,
                'initial_turn':initial_turn,'status':result.status,'decisions':result.decisions,'verdict':verdict.get('outcome'),
                'contacts':sum(bool(r['result'].get('collided')) for r in rows),
                'pickup_steps':[r['step'] for r in rows if r['tool']=='pickup' and r['result'].get('success')],
                'drop_steps':[r['step'] for r in rows if r['tool']=='drop' and r['result'].get('in_zone')],
                'run_dir':str(result.run_dir),'capture':verdict.get('pov_status')}
        Path(output,'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
        assert verdict.get('outcome')=='pass' and report['contacts']==0,report
    finally:
        operator.operator('/run/finish',{'reason':'abandoned'})
        operator.operator('/run/reset',{})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--engine-port',type=int,required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--command',default='Fetch the red sphere and drop it in the green zone')
    p.add_argument('--initial-turn',type=float,default=0)
    a=p.parse_args();verify(a.engine_port,a.output,a.command,a.initial_turn)
