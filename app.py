import argparse
import json
import os
from threading import Thread

import aiohttp_cors
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from flask import Flask
from flask_sockets import Sockets
from gevent import pywsgi
from geventwebsocket.handler import WebSocketHandler
import asyncio

from core.mike import MikeListener
from core.stream_track import MetaHumanPlayer
from links import implement

app = Flask(__name__)
sockets = Sockets(app)

#####webrtc###############################
pcs = set()


# @app.route('/offer', methods=['POST'])
async def offer(request):
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
    
    iceServers = [RTCIceServer(urls=["stun:stun.l.google.com:19302"]), RTCIceServer(urls=["turn:turn.service.yongdao365.com:3478"],username="yongdao",credential="VM5LVDn8fe")]

    # Debugging: Print the iceServers configuration
    print("ICE Servers Configuration:", iceServers)

    configuration = RTCConfiguration(iceServers=iceServers)

    # Debugging: Check if the configuration is set correctly
    print("RTC Configuration:", configuration)

    pc = RTCPeerConnection(configuration=configuration)
#    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        print("Connection state is %s" % pc.connectionState)
        if pc.connectionState == "failed":
            await pc.close()
            pcs.discard(pc)

    player = MetaHumanPlayer(link)
    audio_sender = pc.addTrack(player.audio)
    video_sender = pc.addTrack(player.video)

    await pc.setRemoteDescription(offer)

    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.Response(
        content_type="application/json",
        text=json.dumps(
            {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
        ),
    )


@sockets.route('/humanecho')
def echo_socket(ws):
    # 获取WebSocket对象
    # ws = request.environ.get('wsgi.websocket')
    # 如果没有获取到，返回错误信息
    if not ws:
        print('未建立连接！')
        return 'Please use WebSocket'
    # 否则，循环接收和发送消息
    else:
        print('建立连接！')
        while True:
            message = ws.receive()
            print('Received:', message)

            if message:
                asyncio.get_event_loop().run_until_complete(link.say(message))
            else:
                return '输入信息为空'


async def on_shutdown(app):
    # close peer connections
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()


##########################################

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--real_fps', type=int, default=25)
    parser.add_argument('--mike', action='store_true', help="start mike listen")
    parser.add_argument('--tts', type=str, default='edgetts')  # xtts
    parser.add_argument('--link_name', type=str, default='ErNerf', help="Choose Link")  # ErNerf SyncTalk
    parser.add_argument('--model_name', type=str, default='obama')
    opt = parser.parse_args()
    opt.base_dir = os.path.dirname(os.path.abspath(__file__))  # root
    opt.real_fps = 15
    opt.real_fps = min(opt.real_fps, 25)  # <=25
    if opt.real_fps >= 25:
        opt.block_mode = False
    else:
        opt.block_mode = True

    print('Block Mode: {}'.format(opt.block_mode))

    # aiortc
    web_app = web.Application()
    web_app.on_shutdown.append(on_shutdown)
    web_app.router.add_post("/offer", offer)
    web_app.router.add_static('/', path=os.path.join(opt.base_dir, 'web'))
    cors = aiohttp_cors.setup(web_app, defaults={
            "*": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
            )
        })
    for route in list(web_app.router.routes()):
        cors.add(route)


    def run_server(runner):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, '0.0.0.0', 8010)
        loop.run_until_complete(site.start())
        loop.run_forever()


    Thread(target=run_server, args=(web.AppRunner(web_app),)).start()

    link = getattr(implement, opt.link_name + "Link")(opt)
    if not link:
        raise "Link not found"
    if not os.path.exists(link.opt.template) and opt.block_mode:
        link.process_silence_template_video(output_path=link.opt.template, num=300, start_idx=0)

    if opt.mike:
        mike_listener = MikeListener(loop=asyncio.get_event_loop(), link=link, tts_type=opt.tts)
        mike_listener.start()

    print('start websocket server')
    server = pywsgi.WSGIServer(('0.0.0.0', 30003), app, handler_class=WebSocketHandler)
    server.serve_forever()
