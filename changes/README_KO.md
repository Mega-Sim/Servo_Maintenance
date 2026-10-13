# Controller diff 안내

`0005-ecmaster-foe-firmware-update.diff`는 업로드된 최신 `master-controller-260901.zip`을 기준으로 생성했습니다.

변경 대상은 다음 10개 파일입니다.

- `src/app/amc/amc_oht.cpp`
- `src/control/axes_module.cpp`
- `src/control/axes_module.h`
- `src/control/ec_servo_motor.cpp`
- `src/control/ec_servo_motor.h`
- `src/ecmaster/ecat_data_struct.h`
- `src/ecmaster/ecmaster_message_handler.cpp`
- `src/ecmaster/ecmaster_service.cpp`
- `src/ecmaster/ecmaster_service.h`
- `src/ecmaster/ecmaster_servo_test.cpp`

적용 전 확인:

```bash
patch --dry-run -p1 -d /home/semes/work/master_control < 0005-ecmaster-foe-firmware-update.diff
```

전체 변경 파일은 `controller_patch/src/`에도 동일하게 포함되어 있습니다.
