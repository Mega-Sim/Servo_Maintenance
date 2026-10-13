"""Patch-contract tests; does not simulate real EtherCAT hardware."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def source(name):
    candidate = ROOT / name
    if candidate.exists():
        return candidate.read_text()
    # GitHub publishes the controller changes as a diff; after applying it
    # to a local controller tree, the exact sources are available under src/.
    patch = ROOT / 'changes/0006-rsa-pack-profile-sdo-safety.diff'
    if patch.exists():
        return patch.read_text()
    raise IOError('Neither source nor incremental patch was found: ' + name)


class MaintenanceProfileStaticTest(unittest.TestCase):
    def test_product_code_transport_via_existing_rpc(self):
        proto = source('src/ecmaster/rpc/ecmaster.proto')
        self.assertIn('uint32 product_code = 9;', proto)
        self.assertIn('reply->set_product_code(res.res.product_code)', source('src/ecmaster/ecmaster_admin.cpp'))
        self.assertIn('reply.product_code()', source('src/ecmaster/ecmaster_rpc_client.h'))
        self.assertIn('product_code = res.product_code;', source('src/control/ec_servo_motor.cpp'))

    def test_vendor_product_branches(self):
        text = source('src/app/amc/amc_oht.cpp')
        for code in ('0x02020012', '0x02020013', '0x02020033', '0x02010013',
                     '0x02200901', '0x02200902', 'LS_IX7MA4444B_PRODUCT_CODE'):
            self.assertIn(code, text)
        self.assertIn('LS_UNKNOWN_PRODUCT', text)
        self.assertIn('ERROR NOT_IMPLEMENTED', text)

    def test_quad_absolute_only_and_original_legacy_mapping(self):
        master = source('src/ecmaster/ecmaster_message_handler.cpp')
        self.assertIn('slave.product_code == 0x02020033', master)
        self.assertIn('msg.sdo.absolute_index == 1', master)
        self.assertIn('sdo_offset = 0x800;', master)
        self.assertIn('sdo_offset = 0x1000;', master)
        self.assertIn('index += slave.module_num * sdo_offset;', master)
        self.assertIn('index == 0x100A || index == 0x1010', master)

    def test_sdodata_bounds_and_read_failures(self):
        self.assertIn('EM_MSG_Q_DATA_SIZE      = 128', source('src/ecmaster/ecmaster_config.h'))
        motor = source('src/control/ec_servo_motor.cpp')
        self.assertIn('> EM_MSG_Q_DATA_SIZE', motor)
        self.assertIn('res.sdo.length > sizeof(res.sdo.data)', motor)
        amc = source('src/app/amc/amc_oht.cpp')
        self.assertIn('"ERROR PARAM_READ Axis="', amc)
        self.assertIn('SKIP=', amc)
        self.assertIn('FOE_4AXIS_NOT_VALIDATED', amc)

    def test_quad_axis_prefix_mapping(self):
        for axis in range(4):
            self.assertEqual(hex(0x2000 + axis * 0x1000),
                             ('0x2000', '0x3000', '0x4000', '0x5000')[axis])


if __name__ == '__main__':
    unittest.main()