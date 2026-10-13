/*
 * SEMES CONFIDENTIAL
 *
 * Copyright (c) 2017 SEMES Co.,Ltd.
 * All Rights Reserved.
 *
 * NOTICE: All information contained herein is, and remains the property
 * of SEMES and its suppliers, if any. The intellectual and technical concepts
 * contained herein are proprietary to SEMES and its suppliers and may be
 * covered by S.Korea and Foreign Patents, patents in process, and are
 * protected by trade secret or copyright law.
 * Dissemination of this information or reproduction of this material is
 * strictly forbidden unless prior written permission is obtained from SEMES.
 */
//
// Created by master on 17. 4. 13.
//
#include "emul/ecat_emulator.h"
#include "ecmaster_service.h"
#include <fstream>
#include <iterator>
#include <sys/stat.h>
#include <limits.h>
#include <strings.h>
#include <vector>


namespace sephi
{

    static bool checkIfUserAlive(pid_t userpid)
    {
        char buff[32];
        snprintf(buff, sizeof(buff), "/proc/%d", userpid);
        return access(buff, R_OK) == 0;
    }

    static void doPrintHex(const char*msg, const void* data, int len) {
        uint8_t *u8 = (uint8_t*)data;
        printf("[[ %s ]]\n", msg);
        for(int i = 0; i < len; i++) {
            if (i>0 && i%4 == 0) printf(" ");
            printf("%02x ", u8[i]);
        }
        printf("\n");
    }

    static void printHex(const char*msg, const void* data, int len) {
#if STK_IO_LOG_ENABLE
        doPrintHex(msg,data,len);
#endif
    }


    bool EcMasterSvc::readOhtFrontInData(OhtFrontIoPdoIn &in)
    {
        SEP_REQUIRE_RET(oht_front_io_ != nullptr, false);
        memcpy(&in, in_pdo+oht_front_io_->in_pd_offset, sizeof(in));

        return true;
    }

    bool EcMasterSvc::readOhtRearInData(OhtRearIoPdoIn &in)
    {
        SEP_REQUIRE_RET(oht_rear_io_ != nullptr, false);
        memcpy(&in, in_pdo+oht_rear_io_->in_pd_offset, sizeof(in));

        return true;
    }

    bool EcMasterSvc::readOhtFrontOutData(OhtFrontIoPdoOut &out)
    {
        SEP_REQUIRE_RET(oht_front_io_ != nullptr, false);
        memcpy(&out, shm_->address + EM_SHM_SECTION_SIZE + oht_front_io_->out_pd_offset, sizeof(out));

        return true;
    }

    bool EcMasterSvc::readOhtRearOutData(OhtRearIoPdoOut &out)
    {
        SEP_REQUIRE_RET(oht_rear_io_ != nullptr, false);
        memcpy(&out, shm_->address + EM_SHM_SECTION_SIZE + oht_rear_io_->out_pd_offset, sizeof(out));

        return true;
    }

    bool EcMasterSvc::writeOhtFrontOutData(const OhtFrontIoPdoOut &out)
    {
        SEP_REQUIRE_RET(oht_front_io_ != nullptr, false);

        memcpy(shm_->address + EM_SHM_SECTION_SIZE + oht_front_io_->out_pd_offset, &out, sizeof(out));
        return true;
    }

    bool EcMasterSvc::writeOhtRearOutData(const OhtRearIoPdoOut &out)
    {
        SEP_REQUIRE_RET(oht_rear_io_ != nullptr, false);

        memcpy(shm_->address + EM_SHM_SECTION_SIZE + oht_rear_io_->out_pd_offset, &out, sizeof(out));
        return true;
    }

    bool EcMasterSvc::readStockerInData(StockerIoPdoIn &in)
    {
        SEP_REQUIRE_RET(stocker_io_ != nullptr, false);
        //STK_IO_LOGT("readStockerInData\n");
        memcpy(&in, in_pdo+stocker_io_->in_pd_offset, sizeof(in));

        printHex("readStockerIn", &in, sizeof(in));
        return true;
    }
    bool EcMasterSvc::readStockerOutData(StockerIoPdoOut &in)
    {
        SEP_REQUIRE_RET(stocker_io_ != nullptr, false);
        //STK_IO_LOGT("readStockerOutData\n");
        memcpy(&in, shm_->address + EM_SHM_SECTION_SIZE + stocker_io_->out_pd_offset, sizeof(in));
        printHex("readStockerOut", &in, sizeof(in));
        return true;
    }

    bool EcMasterSvc::writeStockerOutData(const StockerIoPdoOut &out)
    {
        SEP_REQUIRE_RET(stocker_io_ != nullptr, false);
        STK_IO_LOGT("writeStockerOutData\n");
        memcpy(shm_->address + EM_SHM_SECTION_SIZE + stocker_io_->out_pd_offset, &out, sizeof(out));
        printHex("writeStockerOut", &out, sizeof(out));
        return true;
    }

    bool EcMasterSvc::handleRequestMessage(const ECatSdoMsg& msg, ECatSdoMsg &res)
    {
        res.command = msg.command + msg.RESPONSE;
        res.slave_id = -1;

        if (msg.slave_id >= 0 && msg.slave_id < slaves_.size()) {
            auto& slave = slaves_[msg.slave_id];
            if (slave.user_pid != 0 && !checkIfUserAlive(slave.user_pid)) { // collect zombie resource
                SEP_LOGT("Collecting resource from zombie pid %d\n", slave.user_pid);
                slave.user_pid = 0;
            }
            if (slave.user_pid == 0) {
                slave.user_pid = msg.req.pid;
                res.slave_id = msg.slave_id;
                res.res.in_pd_offset = slave.in_pd_offset;
                res.res.out_pd_offset = slave.out_pd_offset;
                res.res.bus_cycle_us = opt_->dwBusCycleTimeUsec;
                res.res.bus_index = slave.bus_index;
                res.res.servo_module_num = slave.module_num;
                res.res.servo_vendor = slave.vendor_id;
                res.res.slave_type = slave.type;
                res.res.product_code = slave.product_code;

                SEP_LOGI("slave %d is allocated to %d!\n", slave.slave_id, slave.user_pid );
            } else {
                SEP_LOGE("requested slave id (%d) is already in use. \n", msg.slave_id);
                return false;
            }
        } else {
            SEP_LOGE("requested slave id (%d) range invalid. \n", msg.slave_id);
            return false;
        }
        return true;

    }

    bool EcMasterSvc::handleReleaseMessage(const ECatSdoMsg& msg, ECatSdoMsg &res)
    {
        res.command = msg.command + msg.RESPONSE;
        res.slave_id = -1;

        if (msg.slave_id >= 0 && msg.slave_id < slaves_.size()) {
            auto& slave = slaves_[msg.slave_id];
            if (slave.user_pid == msg.req.pid) {
                slave.user_pid = 0;
                res.slave_id = msg.slave_id;
                if (slave.context != -1) {
                    servos_[slave.context].controlWord().command = Cia402ControlWord::QUICK_STOP;
                }
                SEP_LOGI("slave %d is released!\n", slave.slave_id);
            } else {
                SEP_LOGE("release slave id (%d) owner is different. owner pid=%d, your pid=%d\n", msg.slave_id, slave.user_pid, msg.req.pid);
                return false;
            }
        } else {
            SEP_LOGE("release slave id (%d) range invalid. \n", msg.slave_id);
            return false;
        }
        return true;
    }

    bool EcMasterSvc::handleSdoMessage(const ECatSdoMsg& msg)
    {
        ECatSdoMsg res(static_cast<ECatSdoMsg::Command>(msg.command + msg.RESPONSE), msg.slave_id);
        if (msg.slave_id < 0 || msg.slave_id >= static_cast<int32_t>(slaves_.size())) {
            SEP_LOGE("SDO request invalid slave=%d", msg.slave_id);
            return false;
        }
        auto& slave = slaves_[msg.slave_id];
        res.result = -1;
        if (msg.sdo.length == 0 || msg.sdo.length > sizeof(msg.sdo.data)) {
            SEP_LOGE("SDO invalid transfer length: slave=%d len=%u capacity=%zu",
                     msg.slave_id, msg.sdo.length, sizeof(msg.sdo.data));
            slave.msgq->put(res);
            return false;
        }
        int32_t index = msg.sdo.index;
        const bool rsa_quad = (slave.vendor_id == ecvendor_rsa &&
                               slave.product_code == 0x02020033);
        if (msg.sdo.absolute_index == 1) {
            // Opt-in for maintenance only. Ordinary legacy PDO/SDO consumers retain
            // the original module offset calculation without any behavior change.
            const bool pack_level = (index == 0x100A || index == 0x1010);
            const bool axis_level = (index >= 0x2000 && index < 0x6000 &&
                 (static_cast<int>(index >> 12) - 2) == slave.module_num);
            if (!rsa_quad || (!pack_level && !axis_level)) {
                SEP_LOGE("SDO absolute index rejected: slave=%d vendor=0x%x product=0x%x module=%d index=0x%x",
                         msg.slave_id, slave.vendor_id, slave.product_code, slave.module_num, index);
                slave.msgq->put(res);
                return false;
            }
        } else {
            // Existing RSA 2+1+1/Yaskawa/LS module rules are NOT modified.
            int sdo_offset = 0x800;
            if (slave.vendor_id == ecvendor_rsa &&
                msg.sdo.index >= 0x2000 && msg.sdo.index < 0x3000)
                sdo_offset = 0x1000;
            index += slave.module_num * sdo_offset;
        }

        if (msg.command == ECatSdoMsg::SDO_DOWNLOAD) {
            const EC_T_DWORD error = ecatCoeSdoDownloadEx(
                slave.bus_index, index, msg.sdo.subindex,
                (uint8_t *)&msg.sdo.data, msg.sdo.length, msg.sdo.timeout, 0);
            if (error == EC_E_NOERROR) {
                res.result = 0;
            } else {
                SEP_LOGE("SDO download failed slave=%d bus=%d module=%d src=0x%x tx=0x%x sub=%u len=%u code=0x%x",
                         msg.slave_id, slave.bus_index, slave.module_num, msg.sdo.index,
                         index, msg.sdo.subindex, msg.sdo.length, error);
            }
        } else if (msg.command == ECatSdoMsg::SDO_UPLOAD) {
            EcDWord outlen = 0;
            const EC_T_DWORD error = ecatCoeSdoUploadEx(
                slave.bus_index, index, msg.sdo.subindex,
                (uint8_t *)&res.sdo.data, msg.sdo.length, &outlen, msg.sdo.timeout, 0);
            if (error == EC_E_NOERROR && outlen > 0 && outlen <= sizeof(res.sdo.data)
                && outlen <= msg.sdo.length) {
                res.sdo.length = static_cast<uint16_t>(outlen);
                // Mark successful CoE read (res.result aliases sdo.timeout).
                res.result = 0;
            } else {
                SEP_LOGE("SDO upload failed slave=%d bus=%d module=%d src=0x%x tx=0x%x sub=%u requested=%u received=%u code=0x%x",
                         msg.slave_id, slave.bus_index, slave.module_num, msg.sdo.index,
                         index, msg.sdo.subindex, msg.sdo.length, outlen, error);
                res.result = -1;
            }
        } else {
            SEP_LOGE("Unexpected SDO command=%u", msg.command);
            slave.msgq->put(res);
            return false;
        }
        slave.msgq->put(res);
        return true;
    }

    bool EcMasterSvc::handleFirmwareDownload(const ECatSdoMsg& msg)
    {
        ECatSdoMsg res(ECatSdoMsg::RESPONSE_FIRMWARE_DOWNLOAD, msg.slave_id);
        res.result = -1;

        if (msg.slave_id < 0 || msg.slave_id >= static_cast<int32_t>(slaves_.size())) {
            // No reply queue exists for an unknown slave.  AMC only sends valid axes.
            SEP_LOGE("Firmware update axis is invalid: %d\n", msg.slave_id);
            return false;
        }
        auto& slave = slaves_[msg.slave_id];
        if (firmware_update_in_progress_) {
            SEP_LOGE("Another firmware update is already running\n");
            slave.msgq->put(res);
            return false;
        }

        char real_path[PATH_MAX] = {0,};
        if (realpath(msg.firmware.path, real_path) == nullptr) {
            SEP_LOGE("Firmware file cannot be resolved: %s\n", msg.firmware.path);
            slave.msgq->put(res);
            return false;
        }
        const char allowed_dir[] = "/home/semes/work/bin/";
        if (strncmp(real_path, allowed_dir, sizeof(allowed_dir) - 1) != 0) {
            SEP_LOGE("Firmware file is outside work/bin: %s\n", real_path);
            slave.msgq->put(res);
            return false;
        }
        const char* filename = strrchr(real_path, '/');
        filename = filename == nullptr ? real_path : filename + 1;
        const size_t filename_len = strlen(filename);
        if (filename_len < 5 || strcasecmp(filename + filename_len - 4, ".bin") != 0) {
            SEP_LOGE("Firmware file extension is invalid: %s\n", filename);
            slave.msgq->put(res);
            return false;
        }

        struct stat file_stat = {};
        const size_t max_firmware_size = 8U * 1024U * 1024U;
        if (stat(real_path, &file_stat) != 0 || !S_ISREG(file_stat.st_mode)
            || file_stat.st_size <= 0 || static_cast<size_t>(file_stat.st_size) > max_firmware_size) {
            SEP_LOGE("Firmware file size/type is invalid: %s\n", real_path);
            slave.msgq->put(res);
            return false;
        }

        std::ifstream input(real_path, std::ios::binary);
        std::vector<EC_T_BYTE> firmware(
            (std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
        if (!input.eof() || firmware.size() != static_cast<size_t>(file_stat.st_size)) {
            SEP_LOGE("Firmware file read failed: %s\n", real_path);
            slave.msgq->put(res);
            return false;
        }

        const EC_T_DWORD acontis_slave_id = ecatGetSlaveId(slave.station_address);
        if (acontis_slave_id == INVALID_SLAVE_ID) {
            SEP_LOGE("Acontis slave ID lookup failed: station=%d\n", slave.station_address);
            slave.msgq->put(res);
            return false;
        }

        SEP_LOGI("[SMT] FOE_START axis=%d station=%d product=0x%x bytes=%zu",
                 msg.slave_id, slave.station_address, slave.product_code, firmware.size());
        firmware_update_in_progress_ = true;
        stopAllServo();
        SEP_LOGI("[SMT] FOE_STAGE stage=stop_all_servo axis=%d", msg.slave_id);
        EC_T_DWORD error = emSetSlaveState(
            INSTANCE_MASTER_DEFAULT, acontis_slave_id, DEVICE_STATE_INIT, 30000);
        SEP_LOGI("[SMT] FOE_STAGE stage=init axis=%d code=0x%08lx",
                 msg.slave_id, static_cast<unsigned long>(error));
        if (error == EC_E_NOERROR) {
            error = emSetSlaveState(
                INSTANCE_MASTER_DEFAULT, acontis_slave_id, DEVICE_STATE_BOOTSTRAP, 30000);
            SEP_LOGI("[SMT] FOE_STAGE stage=bootstrap axis=%d code=0x%08lx",
                     msg.slave_id, static_cast<unsigned long>(error));
        }
        if (error == EC_E_NOERROR) {
            SEP_LOGI("[SMT] FOE_STAGE stage=download_begin axis=%d", msg.slave_id);
            const EC_T_DWORD timeout = msg.firmware.timeout_ms == 0 ? 900000 : msg.firmware.timeout_ms;
            error = emFoeFileDownload(
                INSTANCE_MASTER_DEFAULT,
                acontis_slave_id,
                const_cast<EC_T_CHAR*>(filename),
                static_cast<EC_T_DWORD>(filename_len),
                firmware.data(),
                static_cast<EC_T_DWORD>(firmware.size()),
                0x01020304,
                timeout);
            SEP_LOGI("[SMT] FOE_STAGE stage=download_end axis=%d code=0x%08lx",
                     msg.slave_id, static_cast<unsigned long>(error));
        }

        SEP_LOGI("[SMT] FOE_STAGE stage=restore_init axis=%d", msg.slave_id);
        EC_T_DWORD restore_error = emSetSlaveState(
            INSTANCE_MASTER_DEFAULT, acontis_slave_id, DEVICE_STATE_INIT, 30000);
        SEP_LOGI("[SMT] FOE_STAGE stage=restore_init_done axis=%d code=0x%08lx",
                 msg.slave_id, static_cast<unsigned long>(restore_error));
        if (error == EC_E_NOERROR && restore_error == EC_E_NOERROR) {
            restore_error = emSetSlaveState(
                INSTANCE_MASTER_DEFAULT, acontis_slave_id, DEVICE_STATE_PREOP, 30000);
            SEP_LOGI("[SMT] FOE_STAGE stage=preop axis=%d code=0x%08lx",
                     msg.slave_id, static_cast<unsigned long>(restore_error));
        }
        if (error == EC_E_NOERROR && restore_error == EC_E_NOERROR) {
            uint8_t reset_value = 1;
            const uint16_t reset_index =
                (slave.product_code == 0x02020013 || slave.product_code == 0x02020012)
                ? 0x2c10 : 0x3010;
            restore_error = ecatCoeSdoDownloadEx(
                slave.bus_index, reset_index, 0, &reset_value, sizeof(reset_value), 30000, 0);
            SEP_LOGI("[SMT] FOE_STAGE stage=reset_sdo axis=%d index=0x%04x code=0x%08lx",
                     msg.slave_id, reset_index, static_cast<unsigned long>(restore_error));
            usleep(6000000);
        }

        SEP_LOGI("[SMT] FOE_STAGE stage=restore_op axis=%d", msg.slave_id);
        const bool op_restored = changeECatState(eEcatState_OP);
        firmware_update_in_progress_ = false;
        recover_in_process_ = false;

        SEP_LOGI("[SMT] FOE_RESTORE_STATUS axis=%d foe_code=0x%08lx restore_code=0x%08lx op=%d",
                 msg.slave_id, static_cast<unsigned long>(error),
                 static_cast<unsigned long>(restore_error), op_restored ? 1 : 0);
        if (error == EC_E_NOERROR && restore_error == EC_E_NOERROR && op_restored) {
            res.result = 0;
            SEP_LOGI("Firmware update completed: axis=%d station=%d file=%s\n",
                msg.slave_id, slave.station_address, filename);
        } else {
            SEP_LOGE("Firmware update failed: axis=%d foe=0x%08lx restore=0x%08lx op=%d\n",
                msg.slave_id, static_cast<unsigned long>(error),
                static_cast<unsigned long>(restore_error), op_restored ? 1 : 0);
        }
        slave.msgq->put(res);
        return res.result == 0;
    }

    bool EcMasterSvc::handleBitOpMessage(const ECatSdoMsg& msg)
    {
        ECatSdoMsg res(static_cast<ECatSdoMsg::Command>(msg.command + msg.RESPONSE), msg.slave_id);
        auto& slave = slaves_[msg.slave_id];
        auto& servo = servos_[slave.context];
        if (msg.command == msg.CLEAR_BITS) {
            servo.statusWord().ref() &= ~msg.word_bits;
            SEP_LOGT("Clearing status 0x%x\n", msg.word_bits);
        } else {
            servo.statusWord().ref() |= msg.word_bits;
            SEP_LOGT("Setting status 0x%x\n", msg.word_bits);
        }
        res.result = 0;
        slave.msgq->put(res);
        return true;
    }


    bool EcMasterSvc::handleSetMasterAxisMessage(const ECatSdoMsg &msg)
    {
        ECatSdoMsg res(static_cast<ECatSdoMsg::Command>(msg.command + msg.RESPONSE), msg.slave_id);
        auto& slave = slaves_[msg.slave_id];

        res.result = -1;

        if (msg.master_axis < 0) {
            SEP_LOGT("Unfollow axis %d\n", msg.slave_id);
            slave.following_axis = -1;
            auto& servo = servos_[slave.context];
            servo.setVelocityTarget(0); // for stop axis
            servo.setTorqueTarget(0);
            res.result = 0;
        } else if (msg.master_axis >= slaves_.size()) {
            SEP_LOGE("Master Axis range (%d) is invalid!\n", msg.master_axis);
        } else {
            auto &sync_slave = slaves_[msg.master_axis];
            if (slave.type != SlaveType::CIA402_COMPLIANT_SERVO_SINGLE
            && slave.type != SlaveType::CIA402_COMPLIANT_SERVO_MULTI) {
                SEP_LOGE("Slave %d is not servo drive\n", slave.slave_id);
            } else if (sync_slave.type != SlaveType::CIA402_COMPLIANT_SERVO_SINGLE
            && sync_slave.type != SlaveType::CIA402_COMPLIANT_SERVO_MULTI) { // yjchoe
                SEP_LOGE("Master Slave %d is not servo drive\n", sync_slave.slave_id);
            } else if (sync_slave.following_axis != -1) {
                SEP_LOGE("Master Slave %d is following %d. Circulation is prevented.\n", sync_slave.slave_id, sync_slave.following_axis);
            } else {
                slave.following_axis = msg.master_axis;
                SEP_LOGT("slave %d is following %d\n", slave.slave_id, slave.following_axis);
                res.result = 0;
            }
        }
        slave.msgq->put(res);
        return true;
    }

    bool EcMasterSvc::handleMessages()
    {
        ECatSdoMsg msg;
        bool ret;
        while(true) {
            if (!msgq_->get(msg, 1.0)) {
                break;
            }
            ret = true; // by default
            switch(msg.command) {
                case ECatSdoMsg::RECOVER_OP_MODE:
                    SEP_LOGT("handleMessages: ECMaster State change to OP\n");
                    changeECatState(eEcatState_OP);
                    ret = true;
                    recover_in_process_ = false;
                    break;
                case ECatSdoMsg::CLEAR_BITS:/* fall through */
                case ECatSdoMsg::SET_BITS:
                    ret = handleBitOpMessage(msg);
                    break;
                /* 
                case ECatSdoMsg::SET_MASTER_AXIS:    
                    ret = handleSetMasterAxisMessage(msg);
                    break;
                */
                case ECatSdoMsg::SDO_DOWNLOAD: /* fall through */
                case ECatSdoMsg::SDO_UPLOAD: {
#if 0 // async execution
                    auto job = new AsyncJobHandle();
                    *job =  std::async(std::launch::async, [this, &msg, &job](){
                        SEP_LOGT("Async SDO job %p begins\n", job );
                        bool result = handleSdoMessage(msg);
                        ECatSdoMsg req(ECatSdoMsg::REMOVE_HANDLE); req.handle = job;
                        msgq_->put(req);
                        return result;
                    });
                    async_jobs_.insert(job);
#else
                    ret = handleSdoMessage(msg);
#endif
                    break;
                }
                case ECatSdoMsg::FIRMWARE_DOWNLOAD:
                    ret = handleFirmwareDownload(msg);
                    break;
                case ECatSdoMsg::REMOVE_HANDLE: {
                    auto job = (AsyncJobHandle*)msg.handle;
                    SEP_LOGT("Async SDO job %p erased\n", job );
                    async_jobs_.erase(job);
                    delete job;
                    break;
                }
                default:
                    SEP_LOGE("Unexpected sdo message command - %d\n ", msg.command);
            }
            if (!ret) {
                SEP_LOGW("Fail to process sdo message command %d\n", msg.command);
            }
        }
        return true;
    }
}
