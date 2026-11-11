# 高画質録画：Chromeの画面を3倍の画素数でJPEG連番に取り込み、H.264に変換
import base64, os, shutil, subprocess, sys, time
from playwright.sync_api import sync_playwright
url, out, sec = sys.argv[1], sys.argv[2], float(sys.argv[3])
W, H, DPR = 390, 844, 3
fr = "/tmp/frames"; shutil.rmtree(fr, ignore_errors=True); os.makedirs(fr)
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome")
    c = b.new_context(viewport={"width": W, "height": H}, device_scale_factor=DPR, is_mobile=True, has_touch=True)
    pg = c.new_page(); errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(url); pg.wait_for_timeout(2500)
    cdp = c.new_cdp_session(pg); frames = []
    def on(f):
        frames.append((f["metadata"]["timestamp"], f["data"]))
        cdp.send("Page.screencastFrameAck", {"sessionId": f["sessionId"]})
    cdp.on("Page.screencastFrame", on)
    cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 95, "maxWidth": W*DPR, "maxHeight": H*DPR, "everyNthFrame": 1})
    pg.wait_for_timeout(int(sec*1000))
    cdp.send("Page.stopScreencast"); pg.screenshot(path=out.replace(".mp4", ".png")); c.close(); b.close()
print("frames", len(frames), "errors", errs)
# 実時間に合わせて30fpsに並べ直す
t0 = frames[0][0]; n = int((frames[-1][0]-t0)*30); j = 0
for i in range(n):
    t = t0 + i/30
    while j+1 < len(frames) and frames[j+1][0] <= t: j += 1
    open(f"{fr}/{i:05d}.jpg", "wb").write(base64.b64decode(frames[j][1]))
subprocess.run(["ffmpeg","-y","-loglevel","error","-framerate","30","-i",f"{fr}/%05d.jpg",
  "-vf","scale=1080:-2:flags=lanczos","-c:v","libx264","-preset","veryfast","-crf","17","-pix_fmt","yuv420p","-movflags","+faststart",out], check=True)
print(subprocess.run(["ffprobe","-v","error","-show_entries","stream=width,height,r_frame_rate","-of","csv=p=0",out],capture_output=True,text=True).stdout)
