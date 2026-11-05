"""Pixel-grounded control for explicit colored-object fetch commands.

Only the numbered rover image, public pose/holding and optional range are inputs.
No scene file, fixture identity, evaluator state, or preconfigured route is read.
Unsupported/relational commands retain the general model policy.
"""
import heapq
import math
import re
from PIL import Image

COLORS = {'red': lambda h: h < 12 or h > 242,
          'green': lambda h: 70 <= h <= 116,
          'blue': lambda h: 140 <= h <= 178,
          'purple': lambda h: 180 <= h <= 224,
          'yellow': lambda h: 25 <= h <= 52}


def components(image):
    small=image.convert('RGB').resize((160,120)).convert('HSV')
    pixels=small.load(); result=[]
    for color, accepts in COLORS.items():
        remaining={(x,y) for y in range(42,120) for x in range(160)
                   if pixels[x,y][1]>65 and pixels[x,y][2]>45 and accepts(pixels[x,y][0])}
        while remaining:
            start=remaining.pop(); points=[start]; queue=[start]
            while queue:
                x,y=queue.pop()
                for q in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                    if q in remaining:
                        remaining.remove(q);queue.append(q);points.append(q)
            if len(points)<12: continue
            xs,ys=zip(*points)
            box=[min(xs)*4,min(ys)*4,(max(xs)+1)*4,(max(ys)+1)*4]
            fill=len(points)/((max(xs)-min(xs)+1)*(max(ys)-min(ys)+1))
            result.append({'color':color,'box':box,'fill':fill,'count':len(points),
                           'center':sum(xs)/len(xs)*4+2,
                           'points':[(x*4+2,y*4+2) for x,y in points]})
    return result


def ground_point(u,v,state):
    # 640x480, 90-degree horizontal FOV; camera forward .22, height .5, pitch 10deg.
    xn,yn=(u-320)/320,(v-240)/320
    pitch=math.radians(10); down=math.sin(pitch)+yn*math.cos(pitch)
    if down<=.035: return None
    forward=.22+.5*(math.cos(pitch)-yn*math.sin(pitch))/down
    right=.5*xn/down
    if forward<0 or math.hypot(forward,right)>10: return None
    angle=math.radians(state['heading'])
    return (state['x']+forward*math.cos(angle)-right*math.sin(angle),
            state['y']-forward*math.sin(angle)-right*math.cos(angle))


def bearing_to(point,state):
    return math.remainder(-math.degrees(math.atan2(point[1]-state['y'],point[0]-state['x']))-state['heading'],360)


def segment_clear(a,b,obstacles):
    dx,dy=b[0]-a[0],b[1]-a[1]; length=dx*dx+dy*dy
    for x,y,r in obstacles:
        t=max(0,min(1,((x-a[0])*dx+(y-a[1])*dy)/length)) if length else 0
        if math.hypot(a[0]+t*dx-x,a[1]+t*dy-y)<r:
            # A newly expanded uncertainty margin can contain the current pose.
            # Permit only motion strictly away from its center, never through it.
            before=math.hypot(a[0]-x,a[1]-y);after=math.hypot(b[0]-x,b[1]-y)
            if before<r and after>before+.001 and (a[0]-x)*dx+(a[1]-y)*dy>=0:continue
            return False
    return True


def waypoint(start,goal,obstacles):
    if segment_clear(start,goal,obstacles): return goal
    cell=.12
    lowx=min(start[0],goal[0])-1.5; lowy=min(start[1],goal[1])-1.5
    nx=int((abs(start[0]-goal[0])+3)/cell)+1
    ny=int((abs(start[1]-goal[1])+3)/cell)+1
    def grid(p): return (round((p[0]-lowx)/cell),round((p[1]-lowy)/cell))
    def point(q): return (lowx+q[0]*cell,lowy+q[1]*cell)
    first,last=grid(start),grid(goal)
    grid_point=point
    def point(q):return start if q==first else goal if q==last else grid_point(q)
    costs={first:0}; parents={}; todo=[(0,first)]
    while todo:
        _,node=heapq.heappop(todo)
        if node==last:
            path=[goal]
            while node!=first: path.append(point(node));node=parents[node]
            path.reverse();chosen=path[0]
            for p in path:
                if segment_clear(start,p,obstacles): chosen=p
                else: break
            return chosen
        for ox,oy in ((-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)):
            nxt=(node[0]+ox,node[1]+oy)
            if not(0<=nxt[0]<nx and 0<=nxt[1]<ny): continue
            if not segment_clear(point(node),point(nxt),obstacles): continue
            cost=costs[node]+math.hypot(ox,oy)
            if cost>=costs.get(nxt,float('inf')):continue
            costs[nxt]=cost;parents[nxt]=node
            heapq.heappush(todo,(cost+math.hypot(last[0]-nxt[0],last[1]-nxt[1]),nxt))
    return None


class CameraNavigator:
    def __init__(self,command):
        match=re.fullmatch(r'\s*fetch the (red|green|blue|purple|yellow) (sphere|cube|cylinder|cone) and drop it in the (red|green|blue|purple|yellow) zone[.!?]?\s*',command,re.I)
        self.task=tuple(x.lower() for x in match.groups()) if match else None
        self.zone=None; self.zone_score=0; self.obstacles=[];self.last_target=None
        self.scan=0;self.debug={};self.route_target=None

    def decide(self,image_path,state,depth,disabled=()):
        if not self.task:return None
        with Image.open(image_path) as im: seen=components(im)
        color,shape,zone_color=self.task
        holding=state['holding']
        if not holding:
            candidates=[]
            for c in seen:
                if c['color']!=color:continue
                l,t,r,b=c['box'];aspect=(r-l)/(b-t)
                if r-l>580:continue
                matches=(shape=='sphere' and ((.65<aspect<1.5 and c['fill']<.89) or b>=476)
                         or shape=='cube' and c['fill']>=.8
                         or shape=='cone' and c['fill']<.7
                         or shape=='cylinder' and .25<aspect<1.1 and c['fill']>.65)
                if matches:candidates.append(c)
            if not candidates:
                return ('turn',{'degrees':45},'camera_reacquire_object')
            target=max(candidates,key=lambda c:(c['box'][3],c['count']))
            self.last_target=target
            bearing=math.degrees(math.atan((target['center']-320)/320))
            if depth is not None and depth<.8:bearing*=.7
            near=depth is not None and depth<=.10
            if near and abs(bearing)<30 and 'pickup' not in disabled:
                return ('pickup',{},'camera_reach_trial')
            if abs(bearing)>7:
                return ('turn',{'degrees':round(max(-25,min(25,bearing)),2)},'camera_object_alignment')
            step=.25
            if depth is not None and depth<.45:step=.03 if depth<=.25 else .1
            elif target['box'][3]>=476:step=.05
            if depth is not None:step=min(step,max(0,depth-.045))
            if step<.005:return ('turn',{'degrees':8 if bearing>=0 else -8},'camera_near_alignment')
            return ('move',{'distance':math.floor(step*10000)/10000},'camera_object_approach')

        # Observe bounded green/etc floor components; cyan wall trim is a different
        # hue and image-spanning/upper components are excluded. Keep the best view.
        for c in seen:
            l,t,r,b=c['box']
            if c['color']==zone_color and t>190 and r-l<590:
                points=[ground_point(u,v,state) for u,v in c['points']]
                points=[p for p in points if p is not None]
                if len(points)<12:continue
                xs,ys=zip(*points); width,height=max(xs)-min(xs),max(ys)-min(ys)
                area=width*height
                if .05<area<5 and max(width,height)<3 and area>self.zone_score*1.1:
                    middle=((min(xs)+max(xs))/2,(min(ys)+max(ys))/2)
                    self.zone=min(points,key=lambda p:math.dist(p,middle));self.zone_score=area
                continue
            if c['color']==zone_color or t<185 or b>=472 or r-l>320:continue
            point=ground_point(c['center'],b,state)
            if point is None:continue
            distance=math.dist(point,(state['x'],state['y']))
            radius=min(.35,(r-l)*max(.1,distance-.22)/640)+.48
            if distance<radius+.08:continue
            same=next((o for o in self.obstacles if o[3]==c['color'] and math.dist(o[:2],point)<.55),None)
            if same:
                same[0]=(same[0]+point[0])/2;same[1]=(same[1]+point[1])/2;same[2]=max(same[2],radius)
            else:self.obstacles.append([*point,radius,c['color']])
        if self.zone is None:
            self.scan+=1
            return ('turn',{'degrees':45},'camera_find_floor_zone')
        start=(state['x'],state['y']);distance=math.dist(start,self.zone)
        angle=bearing_to(self.zone,state)
        # Attachment calibration is fixed robot geometry, not scene truth.
        carried=(state['x']+.52*math.cos(math.radians(state['heading'])),
                 state['y']-.52*math.sin(math.radians(state['heading'])))
        if math.dist(carried,self.zone)<=.10 and 'drop' not in disabled:
            return ('drop',{},'camera_zone_arrival')
        obstacles=[o[:3] for o in self.obstacles]
        self.debug={'pose':dict(state),'zone':self.zone,'obstacles':list(self.obstacles)}
        target=self.route_target
        if (target is None or math.dist(start,target)<.15
                or not segment_clear(start,target,obstacles)):
            target=waypoint(start,self.zone,obstacles)
            self.route_target=target
        if segment_clear(start,self.zone,obstacles):
            target=self.zone;self.route_target=target
        if target is None:return ('turn',{'degrees':45},'camera_inspect_route')
        angle=bearing_to(target,state)
        if abs(angle)>8:
            return ('turn',{'degrees':round(max(-45,min(45,angle)),2)},'camera_route_alignment')
        step=min(.35,math.dist(start,target))
        if target==self.zone:step=min(step,max(-.1,distance-.52))
        if abs(step)<.01:return ('turn',{'degrees':round(bearing_to(self.zone,state),2)},'camera_drop_alignment')
        return ('move',{'distance':round(step,4)},'camera_zone_approach')
