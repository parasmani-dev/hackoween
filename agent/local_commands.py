"""Deterministic local command grammar. Never evaluates user text as code."""
from dataclasses import dataclass
import math
import re
from .claude import Decision

HELP = ('Local commands: "fetch the red sphere and drop it in the green zone" or '
        '"move forward 1 m, turn left 90 degrees, pick up". '
        'Colors: red, green, blue, purple, yellow. Shapes: sphere/ball, cube/box, cylinder, cone. '
        'Movement requires a positive number and m/metres/cm or degrees. Up to 60 actions.')
COLORS = 'red|green|blue|purple|yellow'
SHAPES = 'sphere|ball|cube|box|cylinder|cone'
NUMBER = r'(?:\d+(?:\.\d+)?|\.\d+)'


@dataclass(frozen=True)
class LocalPlan:
    kind: str
    canonical: str
    actions: tuple


def parse_command(command):
    if not isinstance(command,str) or not command.strip() or len(command)>500:
        raise ValueError('Enter a command of 1–500 characters. '+HELP)
    text=re.sub(r'\s+',' ',command.strip().lower()).rstrip('.!?')
    # Whole-string match prevents silently ignoring unknown suffixes or directions.
    fetch=re.fullmatch(rf'(?:fetch|bring|get|retrieve|pick up|pickup|pick) (?:the |a )?({COLORS}) ({SHAPES}) '
        rf'(?:(?:and|then) (?:drop|deliver|place|put)(?: it)? (?:in|into|to)|to|into|in) '
        rf'(?:the |a )?({COLORS}) (?:zone|drop zone|area)',text)
    if fetch:
        color,shape,zone=fetch.groups();shape={'ball':'sphere','box':'cube'}.get(shape,shape)
        return LocalPlan('fetch',f'Fetch the {color} {shape} and drop it in the {zone} zone',())
    actions=[]
    for part in re.split(r'\s*(?:,|;|\bthen\b|\band\b)\s*',text):
        part=re.sub(r'^rover ','',part.strip())
        if not part:raise ValueError('Empty command step. '+HELP)
        motion=re.fullmatch(rf'(?:move|drive|go) (forward|forwards|backward|backwards|back|reverse) ({NUMBER}) (m|meters?|metres?|cm|centimeters?|centimetres?)',part)
        turn=re.fullmatch(rf'(?:turn|rotate) (left|right|clockwise|counterclockwise|anticlockwise) ({NUMBER}) (?:degrees?|deg)',part)
        if motion:
            direction,raw,unit=motion.groups();distance=float(raw)/(100 if unit.startswith('c') else 1)
            if not math.isfinite(distance) or not 0<distance<=10:raise ValueError('Move distance must be greater than 0 and at most 10 metres.')
            sign=1 if direction.startswith('forward') else -1
            # Small segments preserve fresh observations and collision checks.
            count=math.ceil(distance/.25)
            for i in range(count):actions.append(('move',{'distance':sign*round(min(.25,distance-i*.25),6)}))
        elif turn:
            direction,raw=turn.groups();degrees=float(raw)
            if not math.isfinite(degrees) or not 0<degrees<=360:raise ValueError('Turn must be greater than 0 and at most 360 degrees.')
            sign=-1 if direction in ('left','counterclockwise','anticlockwise') else 1
            for i in range(math.ceil(degrees/45)):actions.append(('turn',{'degrees':sign*min(45,degrees-i*45)}))
        elif part in ('pick up','pickup','grab','pick up object','pick up the object'):
            actions.append(('pickup',{}))
        elif part in ('drop','drop it','release','put down'):
            actions.append(('drop',{}))
        else:raise ValueError('Unsupported or ambiguous local step: '+part[:80]+'. '+HELP)
        if len(actions)>60:raise ValueError('Command expands beyond 60 actions. Use a shorter sequence.')
    return LocalPlan('sequence',text,tuple(actions))


class LocalPolicy:
    """Supplies local intent; the production camera navigator grounds fetch actions."""
    def __init__(self,plan):
        self.plan=plan;self.index=0;self.names=()
        self.stop_on_failure=plan.kind=='sequence'
    def set_available_actions(self,names):self.names=tuple(names)
    @property
    def complete(self):return self.plan.kind=='sequence' and self.index>=len(self.plan.actions)
    def decide(self,messages,*,system):
        if self.plan.kind=='sequence':
            name,args=self.plan.actions[self.index];self.index+=1
            return Decision(name,dict(args),{})
        name='drop' if 'drop' in self.names else 'pickup' if 'pickup' in self.names else 'turn'
        return Decision(name,{'degrees':45} if name=='turn' else {},{})
