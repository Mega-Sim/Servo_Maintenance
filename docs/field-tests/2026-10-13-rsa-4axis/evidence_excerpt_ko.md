# 현장 증거 — 비민감 발췌

자료 출처: 사용자 첨부 `IPC_HTML_UI.png`, `amc_lob.txt`, `ecmaster_lob.txt`. **파일별 로그 시간은 독립적**이며 상호 순서를 맞추지 않음. 아래는 문제 관련 부분만 인용.

## IE 화면에서 확인된 상태
```text
OK
AMC CONNECTED
AXIS 0 SERVO=RSA ECAT=CONNECTED SERVO_ON=0
AXIS 1 SERVO=RSA ECAT=CONNECTED SERVO_ON=0
AXIS 2 SERVO=RSA ECAT=CONNECTED SERVO_ON=0
AXIS 3 SERVO=RSA ECAT=CONNECTED SERVO_ON=0
```
화면에서 선택된 축 표시는 `0 - Front`. 사용자는 별도의 테스트에서 Slide 버전 조회 실패 및 Parameter Read 실패를 보고함. **스냅샷만으로 파라미터 요청 축을 확정하지 않음.**

## AMC 단일 로그: 서보 모듈 매핑
```text
Servo Slave Info: [axis_0] vendor=829 type:1 bus_index:0 module_num:0
Servo Slave Info: [axis_1] vendor=829 type:1 bus_index:0 module_num:1
Servo Slave Info: [axis_2] vendor=829 type:1 bus_index:0 module_num:2
Servo Slave Info: [axis_3] vendor=829 type:1 bus_index:0 module_num:3
```

## EC-Master 단일 로그: 물리 팩/논리축 매핑
```text
RSA Quadruple-Servo Detected
slave_id: 0, station_address: 1001, type: 1
slave_id: 1, station_address: 1001, type: 1
slave_id: 2, station_address: 1001, type: 1
slave_id: 3, station_address: 1001, type: 1
```

## EC-Master 단일 로그: SDO 실패
```text
[ecmaster_message_handler.cpp:223] Fail to upload sdo to slave 1
[ecmaster_message_handler.cpp:223] Fail to upload sdo to slave 3
[ecmaster_message_handler.cpp:223] Fail to upload sdo to slave 3
[ecmaster_message_handler.cpp:223] Fail to upload sdo to slave 3
[ecmaster_message_handler.cpp:223] Fail to upload sdo to slave 0
CEcMbSlave::ProcessCoEResponse(): Size mismatch (max. 1 expected and 2 read)!
CEcMbSlave::ProcessCoEResponse(): Size mismatch (max. 2 expected and 4 read)!
```
원문 전체 기준 Slave 0 실패 **82건**, Slave 1 **1건**, Slave 3 **3건**(위에는 대표 행만 수록). AMC 로그에는 `ec_servo_motor.cpp:197`의 `res.result != -1` 실패 추적 메시지가 **86건** 있음. **양쪽 로그 발생 시각을 서로 대조해 1:1 매칭하지 않음.**

## 보호 정보
원본 EC-Master 로그에 실제 라이선스 키 등 운영 환경 정보가 포함돼 있어 그대로 공개 저장소에 첨부하지 않음. 위 발췌에서 민감한 값은 제외했음.
