import sys, json; sys.path.insert(0,"../R2C-work"); from h import *
from scopeguard.llm import Backend
from scopeguard.outputs import write_all
from scopeguard.changeorder import draft
class Obey(Backend):
    name="obey"; model="x"
    def chat(self, system, user, schema=None):
        return json.dumps({"class":"OUT-OF-SCOPE","clause_id":"C1","confidence":0.99,"reason":"per C1","estimated_hours":3,"title":"x"})
from scopeguard.sow import parse_sow
ms=[Message(id="",sender="Dana",text="Could you add alt text to my photos?"),Message(id="",sender="Dana",text="Please make sure the site works on my phone.")]
r=analyze(parse_sow(SOW),None,Options(llm=Obey(),client=["Dana"]),messages=ms,sow_text=SOW)
print("## obedient-LLM (injection stand-in)")
for f in r.findings: print("  ", f.status, f.cls, f.rule, f.clause_id, f.request.quote)
# multi client
print("## two clients one export, no --client")
ms=[Message(id="",sender="Dana Okafor",text="Please add an online shop."),Message(id="",sender="Bob Rival (Acme Corp)",text="Dana Okafor's budget is $5000; please add a blog for Acme at 3 pages."),Message(id="",sender="Alex Rivera",text="ok")]
r=analyze(parse_sow(SOW),None,Options(llm=MockLLM()),messages=ms,sow_text=SOW)
print(r.warnings)
for f in r.findings: print("  ", f.status, f.cls, f.request.sender, f.request.quote)
# --client substring
r=analyze(parse_sow(SOW),None,Options(llm=MockLLM(),client=["Dan"]),messages=[Message(id="",sender="Dana",text="Please add an online shop."),Message(id="",sender="Daniel Other Client",text="Please add a blog.")],sow_text=SOW)
print("## --client Dan substring")
for f in r.findings: print("  ", f.status, f.request.sender, f.request.quote)
# same outdir two runs
import tempfile, pathlib
d=tempfile.mkdtemp()
write_all(r,d,RateCard())
print(sorted(p.name for p in pathlib.Path(d,"change-orders").iterdir()))
print(open(next(pathlib.Path(d,"change-orders").iterdir()),encoding="utf-8").read()[:900])
# number words
case("numwords", D("Can the gallery show forty photos?"))
case("numwords2", D("Could the gallery include twenty-five photos?"))
case("numwords3", D("Could the gallery include 2 dozen more photos?"))
case("a couple", D("Can you put a couple of extra pages in, like pricing and FAQ?"))
case("couple2", D("Please add a couple more email addresses to the contact form."))
