import sys; sys.path.insert(0,"../R2C-work"); from h import *
from scopeguard.sow import parse_sow
def run2(msgs, **o):
    ms=[Message(id="",sender=s,text=t) for s,t in msgs]
    r=analyze(parse_sow(SOW),None,Options(llm=MockLLM(),**o),messages=ms,sow_text=SOW)
    print(r.warnings)
    for f in r.findings: print("  ",f.status,f.cls,f.request.sender,"|",f.request.quote)
print("## freelancer in SOW, no --client")
run2([("Dana Okafor","Can you add a booking calendar?"),("Alex Rivera","Sure, I'll add a booking calendar for free, no charge. Let me know if you want a blog too, I can add a blog as a bonus."),("Dana Okafor","Great, thanks")])
print("## with --client, freelancer offers free")
run2([("Dana Okafor","Can you add a booking calendar?"),("Alex Rivera","Sure, I'll add that one for free.")],client=["Dana"])
