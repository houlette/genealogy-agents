#!/usr/bin/env python3
"""Save the selected Archion image only after its native-resolution tiles arrive.

Uses the existing signed-in bridge; does not start Chrome or request paid OCR.
Example: python3 tools/browser_bridge/archion_export.py --page 129 --bild 43 \
    --output sources/2026-11-10-export-Musterdorf-Bild43.jpg
"""

import argparse
import base64
import json
from pathlib import Path
import time

import client


STATE = """() => {
 const views=[...document.querySelectorAll('.dvview')];
 const v=views.find(x=>x.getBoundingClientRect().width>0 &&
   window.jQuery?.(x).data('tilezoom.settings'));
 const s=v && jQuery(v).data('tilezoom.settings');
 if (!s) return {url:location.href, ready:false};
 const holder=v.querySelector('.zoom-holder');
 const ts=[...v.querySelectorAll('.zoom-tiles img')];
 // These dimensions and overlap match the loaded tilezoom viewer, including
 // its smaller right/bottom edge tiles. Native zoom excludes digital scaling.
 const scale=2**(s.level-(s.nativeLevels-1));
 const w=Math.ceil(s.width*scale),h=Math.ceil(s.height*scale);
 const expected=Math.ceil(w/s.tileSize)*Math.ceil(h/s.tileSize);
 return {url:location.href,ready:true,
   bild:document.querySelector('.page-select').selectedOptions[0].textContent.trim(),
   width:s.width,height:s.height,level:s.level,nativeLevel:s.nativeLevels-1,
   inAction:s.inAction,holderWidth:parseInt(holder.style.width),
   holderHeight:parseInt(holder.style.height),tileCount:ts.length,expectedTiles:expected,
   image:holder.querySelector('.zoom-thumb').getAttribute('src').split('?')[0]};
}"""

RENDER = """() => {
 if(window.archionExportJob?.status==='loading')
   throw new Error('An export is already running in this tab');
 const job={status:'loading',loaded:0,total:0};window.archionExportJob=job;
 (async () => {
 const v=[...document.querySelectorAll('.dvview')].find(x=>
   x.getBoundingClientRect().width>0 && window.jQuery?.(x).data('tilezoom.settings'));
 const s=jQuery(v).data('tilezoom.settings');
 const imgs=[...v.querySelectorAll('.zoom-tiles img')];
 const expected=Math.ceil(s.width/s.tileSize)*Math.ceil(s.height/s.tileSize);
 if(s.inAction || s.level!==s.nativeLevels-1 || imgs.length!==expected)
   throw new Error('Viewer is not ready at native resolution');
 const thumb=v.querySelector('.zoom-thumb').getAttribute('src').split('?')[0];
 // Off-screen tiles use a transparent GIF in src; _src is the viewer's
 // authorized image URL. Load each one before drawing, with a bounded timeout.
 job.total=imgs.length;
 const loaded=[];let next=0;
 async function worker(){while(job.status==='loading' && next<imgs.length){
   const index=next++,x=imgs[index];
   const tile=await new Promise((resolve,reject)=>{
   const im=new Image();const timer=setTimeout(()=>reject(new Error('Tile load timed out')),30000);
   im.onload=()=>{clearTimeout(timer);
     if(im.naturalWidth<2 || im.naturalHeight<2)reject(new Error('Empty tile'));
     else resolve({im,x});};
   im.onerror=()=>{clearTimeout(timer);reject(new Error('Tile load failed'));};
   im.src=x.getAttribute('_src')||x.src;
   });loaded[index]=tile;job.loaded++;}}
 // Bound network demand; the grid can contain hundreds of off-screen tiles.
 await Promise.all(Array.from({length:12},()=>worker()));
 if(s!==jQuery(v).data('tilezoom.settings') || s.inAction ||
    s.level!==s.nativeLevels-1 || imgs.some(x=>!x.isConnected) ||
    thumb!==v.querySelector('.zoom-thumb').getAttribute('src').split('?')[0])
   throw new Error('Selected image changed during export');
 const c=document.createElement('canvas');c.width=s.width;c.height=s.height;
 const ctx=c.getContext('2d');
 for(const {im,x} of loaded)ctx.drawImage(im,parseFloat(x.style.left),
   parseFloat(x.style.top),parseFloat(x.style.width),parseFloat(x.style.height));
 job.result={width:c.width,height:c.height,tileCount:imgs.length,image:thumb,
   data:c.toDataURL('image/jpeg',0.96)};
 job.status='complete';
 })().catch(e=>{job.status='failed';job.error=e.message;});
 return {status:job.status,total:job.total};
}"""


def image_ready(state, bild):
    return (state.get('ready') and state.get('bild') == str(bild)
            and not state.get('inAction')
            and state.get('level') == state.get('nativeLevel')
            and state.get('holderWidth') == state.get('width')
            and state.get('holderHeight') == state.get('height')
            and state.get('tileCount', 0) > 0
            and state.get('tileCount') == state.get('expectedTiles'))


def wait_image(page, predicate, timeout=20):
    """Wait for this image's tiles, never for whole-page DOM stability."""
    deadline = time.monotonic() + timeout
    while True:
        state = client.evaluate(page, STATE)
        if not isinstance(state, dict):
            raise RuntimeError('Bridge did not return a viewer state object')
        if predicate(state):
            return state
        if time.monotonic() >= deadline:
            raise RuntimeError('Image readiness timed out: ' + json.dumps(state))
        time.sleep(0.4)


def export(page, bild, output):
    output = Path(output)
    manifest_path = output.with_suffix('.json')
    if output.exists() or manifest_path.exists():
        raise FileExistsError('Choose a new filename; existing sources are never overwritten')
    initial = client.evaluate(page, STATE)
    if not isinstance(initial, dict) or '/viewer/churchRegister/' not in initial.get('url', ''):
        raise RuntimeError('Target is not an Archion register viewer')
    client.evaluate(page, "() => {const s=document.querySelector('.page-select');"
                    "const o=[...s.options].find(x=>x.textContent.trim()==="
                    + json.dumps(str(bild)) + ");if(!o)throw new Error('Bild unavailable');"
                    "if(s.value!==o.value){s.value=o.value;s.dispatchEvent(new Event('change',"
                    "{bubbles:true}));}return true;}")
    state = wait_image(page, lambda x: x.get('ready') and x.get('bild') == str(bild)
                       and not x.get('inAction') and x.get('tileCount', 0) > 0)
    # Advance one level at a time; animation-in-progress makes extra clicks
    # silently do nothing. Do not assume a fixed click count reaches full size.
    while state['level'] != state['nativeLevel']:
        prior = state['level']
        button = '.zoom-in' if prior < state['nativeLevel'] else '.zoom-out'
        client.evaluate(page, '() => {document.querySelector('
                        + json.dumps(button) + ').click();return true;}')
        state = wait_image(page, lambda x: x.get('ready') and x.get('bild') == str(bild)
                           and x.get('level') != prior and not x.get('inAction')
                           and x.get('tileCount', 0) > 0
                           and x.get('tileCount') == x.get('expectedTiles'))
    state = wait_image(page, lambda x: image_ready(x, bild))
    client.evaluate(page, RENDER)
    deadline = time.monotonic() + 180
    while True:
        progress = client.evaluate(page, "() => {const j=window.archionExportJob;"
                                   "return {status:j.status,loaded:j.loaded,total:j.total,error:j.error};}")
        if progress['status'] == 'failed':
            raise RuntimeError('Tile export failed: ' + json.dumps(progress))
        if progress['status'] == 'complete':
            break
        if time.monotonic() >= deadline:
            client.evaluate(page, "() => {window.archionExportJob.status='cancelled';return true;}")
            raise RuntimeError('Tile export timed out: ' + json.dumps(progress))
        time.sleep(1)
    result = client.evaluate(page, "() => window.archionExportJob.result")
    if not isinstance(result, dict) or result.get('image') != state['image']:
        raise RuntimeError('Export does not match the selected image')
    data = base64.b64decode(result.pop('data').split(',', 1)[1], validate=True)
    if not data.startswith(b'\xff\xd8') or len(data) < 1000:
        raise RuntimeError('Export is not a usable JPEG')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream:
        stream.write(data)
    result.update(bild=str(bild), source=state['url'], method='native-resolution authorized viewer tiles')
    with manifest_path.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    return {**result, 'file': str(output), 'bytes': len(data)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--page', type=int, required=True, help='Explicit ID of your own Archion tab')
    parser.add_argument('--bild', type=int, required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.page, args.bild, args.output), indent=2))
