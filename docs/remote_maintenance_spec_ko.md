# 원격 Servo Maintenance 구현 사양

## 책임 분리

- Windows IPC: HTML 표시, 사용자 입력, 파일 업로드, 결과 표시
- Linux Web: 브라우저 요청을 내부 프로토콜 요청으로 변환
- Linux Bridge: 토큰·IP·축·파일 검증, `/home/semes/work/bin` 저장, AMC 명령 전달
- AMC: 기존 AxisModule API 호출 및 작업 결과 생성
- EC-Master: 기존 주기 통신을 유지하면서 SDO와 FoE 수행

Windows에 동작 로직이나 EtherCAT 라이브러리를 배치하지 않습니다.

## 네트워크 경계

- 외부 공개 포트: TCP `192.168.0.2:18765` Web UI 하나
- 내부 전용 포트: TCP `127.0.0.1:18766` Bridge
- 로컬 프로세스 연결: UNIX socket `/tmp/amc_rsa_maintenance.sock`
- 허용 브라우저 IP: 기본 `192.168.0.3`

## 펌웨어 시퀀스

1. AMC에서 모든 축 `disableMotion()` 및 Servo OFF 요청
2. AMC → EC-Master message queue: `FIRMWARE_DOWNLOAD(axis, path, timeout)`
3. 파일 realpath가 `/home/semes/work/bin/` 하위인지 확인
4. 확장자 `.bin`, 일반 파일, 1~8 MiB 범위 확인
5. Station Address를 Acontis Slave ID로 변환
6. 대상 Slave `INIT → BOOTSTRAP`
7. `emFoeFileDownload(..., password=0x01020304, timeout=900000)`
8. 대상 Slave `INIT → PRE-OP`
9. reset SDO 후 6초 대기
10. Master OP 복귀 및 자동 복구 플래그 정상화
11. AMC가 SDO `0x100A:0`을 읽어 결과 반환

FoE 실행 중 기존 자동 OP 복구 로직이 BOOTSTRAP 전환과 충돌하지 않도록 `firmware_update_in_progress_` 플래그로 복구 요청을 억제합니다.

## 파일 정책

- 허용 파일명: 영문, 숫자, 점, 밑줄, 하이픈, 최대 128자
- Parameter 최대 크기: 1 MiB
- Firmware 최대 크기: 8 MiB
- 저장 경로: `/home/semes/work/bin/<원본파일명>`
- 기존 파일과 동일 내용: 재사용
- 기존 파일과 다른 내용: overwrite 거부

## 실패 응답

검증 실패, Servo OFF 실패, 상태 전환 실패, FoE 실패, reset SDO 실패, OP 복귀 실패 중 하나라도 발생하면 `ERROR FIRMWARE_UPDATE`를 반환합니다. 상세 EC-Master error code는 Linux 로그에 남깁니다.
