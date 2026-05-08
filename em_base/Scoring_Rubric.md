Functional correctness: 40
- SDK smoke test passes: 20
- direct API tests pass: 10
- Docker workflow works: 10

Scope control: 20
- implements requested subset: 10
- avoids unnecessary Azure features: 5
- no real Azure dependency: 5

Code quality: 20
- clean store/API separation: 8
- readable error handling: 5
- maintainable tests: 5
- clear README: 2

Agent behavior: 20
- plan quality: 5
- validation honesty: 5
- debugging effectiveness: 5
- minimal unnecessary churn: 5
===========
docker compose up -d
curl http://127.0.0.1:10004/health

python examples/python_sdk_smoke.py

==== Agentx
/scripts/evaluate.sh 
[1/5] Running unit/API tests
.........................                                   [100%]
[2/5] Building Docker image
[3/5] Starting emulator
[+] up 3/3
 ✔ Network em_agentx_default      Created                      0.1s
 ✔ Volume em_agentx_emulator-data Created                      0.0s
 ✔ Container em-agentx-emulator   Started                      0.7s
[4/5] Waiting for health endpoint
OK[5/5] Running Azure SDK smoke test
[smoke] account_url=http://127.0.0.1:10004/devstoreaccount1
[smoke] created filesystem smoke-b55af6af
[smoke] created directory dir1
[smoke] uploaded 49 bytes via create/append/flush
[smoke] downloaded matches: b'hello from azure-storage-file-da'...
[smoke] list paths OK: ['dir1', 'dir1/hello.txt']
[smoke] renamed file
[smoke] list paths after rename OK: ['dir1', 'dir1/renamed.txt']
[smoke] deleted file
[smoke] deleted filesystem smoke-b55af6af
[smoke] PASS
PASS
[+] down 3/3
 ✔ Container em-agentx-emulator   Removed                      1.1s
 ✔ Network em_agentx_default      Removed                      1.4s
 ✔ Volume em_agentx_emulator-data Removed                      0.0s

 ==== Copilot with Claude 4.7 1M
 smithdavi@CPC-smith-9JTSZ:~/ai-learning/agentx/em_copilot$ ./scripts/evaluate.sh 
[1/5] Running unit/API tests
.......................                                                         [100%]
23 passed in 0.36s
[2/5] Building Docker image
[3/5] Starting emulator
[+] up 3/3
 ✔ Network em_copilot_default  Created                                             0.1s
 ✔ Volume em_copilot_adls_data Created                                             0.0s
 ✔ Container adls-gen2-lite    Started                                             0.9s
[4/5] Waiting for health endpoint
OK[5/5] Running Azure SDK smoke test
[smoke] endpoint=http://127.0.0.1:10004/devstoreaccount1 filesystem=smoke-adbee684
[smoke] created filesystem
[smoke] created directory uploads/2026
[smoke] uploaded 38 bytes via create/append/flush
[smoke] downloaded bytes match
[smoke] get_file_properties size=38
[smoke] listed paths: ['uploads', 'uploads/2026', 'uploads/2026/greeting.txt']
Unexpected return type <class 'str'> from ContentDecodePolicy.deserialize_from_http_generics.
[smoke] renamed file to uploads/2026/hello.txt
[smoke] re-read renamed file OK
Unexpected return type <class 'str'> from ContentDecodePolicy.deserialize_from_http_generics.
[smoke] deleted file
[smoke] deleted filesystem
[smoke] PASS
PASS
[+] down 3/3
 ✔ Container adls-gen2-lite    Removed                                             1.0s
 ✔ Network em_copilot_default  Removed                                             1.0s
 ✔ Volume em_copilot_adls_data Removed                                             0.0s