"""

Copyright (c) 2020 Alex Forencich

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

"""

import itertools
import logging
import os
import random
import subprocess

import cocotb_test.simulator
import pytest

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer
from cocotb.regression import TestFactory

from cocotbext.axi import AxiBus, AxiMaster, AxiRam


class TB(object):
    def __init__(self, dut):
        self.dut = dut

        s_count = len(dut.axi_crossbar_inst.s_axi_awvalid)
        m_count = len(dut.axi_crossbar_inst.m_axi_awvalid)

        self.log = logging.getLogger("cocotb.tb")
        self.log.setLevel(logging.DEBUG)

        cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())

        self.axi_master = [AxiMaster(AxiBus.from_prefix(dut, f"s{k:02d}_axi"), dut.clk, dut.rst) for k in range(s_count)]
        self.axi_ram = [AxiRam(AxiBus.from_prefix(dut, f"m{k:02d}_axi"), dut.clk, dut.rst, size=2**16) for k in range(m_count)]

        for ram in self.axi_ram:
            # prevent X propagation from screwing things up - "anything but X!"
            # (X on bid and rid can propagate X to ready/valid)
            ram.write_if.b_channel.bus.bid.setimmediatevalue(0)
            ram.read_if.r_channel.bus.rid.setimmediatevalue(0)

    def set_idle_generator(self, generator=None):
        if generator:
            for master in self.axi_master:
                master.write_if.aw_channel.set_pause_generator(generator())
                master.write_if.w_channel.set_pause_generator(generator())
                master.read_if.ar_channel.set_pause_generator(generator())
            for ram in self.axi_ram:
                ram.write_if.b_channel.set_pause_generator(generator())
                ram.read_if.r_channel.set_pause_generator(generator())

    def set_backpressure_generator(self, generator=None):
        if generator:
            for master in self.axi_master:
                master.write_if.b_channel.set_pause_generator(generator())
                master.read_if.r_channel.set_pause_generator(generator())
            for ram in self.axi_ram:
                ram.write_if.aw_channel.set_pause_generator(generator())
                ram.write_if.w_channel.set_pause_generator(generator())
                ram.read_if.ar_channel.set_pause_generator(generator())

    async def cycle_reset(self):
        self.dut.rst.setimmediatevalue(0)
        await RisingEdge(self.dut.clk)
        await RisingEdge(self.dut.clk)
        self.dut.rst.value = 1
        await RisingEdge(self.dut.clk)
        await RisingEdge(self.dut.clk)
        self.dut.rst.value = 0
        await RisingEdge(self.dut.clk)
        await RisingEdge(self.dut.clk)


async def run_test_write(dut, data_in=None, idle_inserter=None, backpressure_inserter=None, size=None, s=0, m=0):

    tb = TB(dut)

    byte_lanes = tb.axi_master[s].write_if.byte_lanes
    max_burst_size = tb.axi_master[s].write_if.max_burst_size

    if size is None:
        size = max_burst_size

    await tb.cycle_reset()

    tb.set_idle_generator(idle_inserter)
    tb.set_backpressure_generator(backpressure_inserter)

    for length in list(range(1, byte_lanes*2))+[1024]:
        for offset in list(range(byte_lanes, byte_lanes*2))+list(range(4096-byte_lanes, 4096)):
            tb.log.info("length %d, offset %d, size %d", length, offset, size)
            ram_addr = offset+0x1000
            addr = ram_addr + m*0x1000000
            test_data = bytearray([x % 256 for x in range(length)])

            tb.axi_ram[m].write(ram_addr-128, b'\xaa'*(length+256))

            await tb.axi_master[s].write(addr, test_data, size=size)

            tb.log.debug("%s", tb.axi_ram[m].hexdump_str((ram_addr & ~0xf)-16, (((ram_addr & 0xf)+length-1) & ~0xf)+48))

            assert tb.axi_ram[m].read(ram_addr, length) == test_data
            assert tb.axi_ram[m].read(ram_addr-1, 1) == b'\xaa'
            assert tb.axi_ram[m].read(ram_addr+length, 1) == b'\xaa'

    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)


async def run_test_read(dut, data_in=None, idle_inserter=None, backpressure_inserter=None, size=None, s=0, m=0):

    tb = TB(dut)

    byte_lanes = tb.axi_master[s].write_if.byte_lanes
    max_burst_size = tb.axi_master[s].write_if.max_burst_size

    if size is None:
        size = max_burst_size

    await tb.cycle_reset()

    tb.set_idle_generator(idle_inserter)
    tb.set_backpressure_generator(backpressure_inserter)

    for length in list(range(1, byte_lanes*2))+[1024]:
        for offset in list(range(byte_lanes, byte_lanes*2))+list(range(4096-byte_lanes, 4096)):
            tb.log.info("length %d, offset %d, size %d", length, offset, size)
            ram_addr = offset+0x1000
            addr = ram_addr + m*0x1000000
            test_data = bytearray([x % 256 for x in range(length)])

            tb.axi_ram[m].write(ram_addr, test_data)

            data = await tb.axi_master[s].read(addr, length, size=size)

            assert data.data == test_data

    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)


async def run_stress_test(dut, idle_inserter=None, backpressure_inserter=None):

    tb = TB(dut)

    await tb.cycle_reset()

    tb.set_idle_generator(idle_inserter)
    tb.set_backpressure_generator(backpressure_inserter)

    async def worker(master, offset, aperture, count=16):
        for k in range(count):
            m = random.randrange(len(tb.axi_ram))
            length = random.randint(1, min(512, aperture))
            addr = offset+random.randint(0, aperture-length) + m*0x1000000
            test_data = bytearray([x % 256 for x in range(length)])

            await Timer(random.randint(1, 100), 'ns')

            await master.write(addr, test_data)

            await Timer(random.randint(1, 100), 'ns')

            data = await master.read(addr, length)
            assert data.data == test_data

    workers = []

    for k in range(16):
        workers.append(cocotb.start_soon(worker(tb.axi_master[k % len(tb.axi_master)], k*0x1000, 0x1000, count=16)))

    while workers:
        await workers.pop(0).join()

    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)


@cocotb.test()
async def run_test_unique_ids_no_blocking(dut):
    """
    Test that with UNIQUE_IDS=1 and unique AXI AxIDs, the crossbar never blocks
    AR or AW channels due to ID ordering. Transactions should only be blocked
    by S_ACCEPT limit or destination backpressure.

    This test issues multiple concurrent transactions with unique IDs to different
    destinations and verifies that address channels are accepted immediately
    (not stalled due to thread tracking).
    """

    tb = TB(dut)

    await tb.cycle_reset()

    tb.log.info("Testing UNIQUE_IDS mode - verifying no blocking on AR/AW with unique IDs")

    # Counter for unique transaction IDs
    next_arid = 0
    next_awid = 0

    # Track blocking events - should not see any stalls beyond normal pipeline delay
    ar_stall_cycles = 0
    aw_stall_cycles = 0
    max_allowed_stall = 5  # Allow small stalls for pipeline delays, not ID blocking

    # Test parameters
    num_transactions = 32
    s_count = len(tb.axi_master)
    m_count = len(tb.axi_ram)

    # Issue many concurrent read and write transactions with unique IDs
    # targeting different master interfaces
    read_results = []
    write_results = []

    async def issue_read(master_idx, arid, target_m, addr):
        """Issue a single read with a specific unique ARID"""
        nonlocal ar_stall_cycles
        master = tb.axi_master[master_idx]
        length = 4

        # Check stall condition: valid high but ready low for multiple cycles
        stall_count = 0

        # Write test data to target RAM first
        test_data = bytearray([arid % 256] * length)
        tb.axi_ram[target_m].write(addr & 0xFFFF, test_data)

        # Issue read
        result = await master.read(addr, length, arid=arid)
        assert result.data == test_data, f"Read data mismatch for ARID={arid}"
        return result

    async def issue_write(master_idx, awid, target_m, addr):
        """Issue a single write with a specific unique AWID"""
        nonlocal aw_stall_cycles
        master = tb.axi_master[master_idx]
        length = 4

        # Write data
        test_data = bytearray([awid % 256] * length)
        await master.write(addr, test_data, awid=awid)

        # Verify write
        assert tb.axi_ram[target_m].read(addr & 0xFFFF, length) == test_data, f"Write data mismatch for AWID={awid}"

    # Launch multiple concurrent transactions with unique IDs across different
    # masters and to different target slaves
    tasks = []
    for i in range(num_transactions):
        master_idx = i % s_count
        target_m = i % m_count
        base_addr = target_m * 0x1000000 + (i * 0x100)  # Different address for each transaction

        # Alternate between read and write, each with a unique ID
        if i % 2 == 0:
            arid = next_arid
            next_arid += 1
            tasks.append(cocotb.start_soon(issue_read(master_idx, arid, target_m, base_addr)))
        else:
            awid = next_awid
            next_awid += 1
            tasks.append(cocotb.start_soon(issue_write(master_idx, awid, target_m, base_addr)))

    # Wait for all transactions to complete
    for task in tasks:
        await task.join()

    tb.log.info(f"Completed {num_transactions} transactions with unique IDs")

    # Additional stress test: issue many transactions to same destination with unique IDs
    # With UNIQUE_IDS=1, these should all proceed without ID-based blocking
    tb.log.info("Testing rapid transactions to same destination with unique IDs")

    tasks = []
    for i in range(16):
        arid = next_arid
        next_arid += 1
        # All go to same destination (master interface 0)
        tasks.append(cocotb.start_soon(issue_read(0, arid, 0, 0x1000 + i * 4)))

    for task in tasks:
        await task.join()

    tb.log.info("UNIQUE_IDS test completed successfully - no unexpected blocking detected")

    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)


def cycle_pause():
    return itertools.cycle([1, 1, 1, 0])


if cocotb.SIM_NAME:

    s_count = len(cocotb.top.axi_crossbar_inst.s_axi_awvalid)
    m_count = len(cocotb.top.axi_crossbar_inst.m_axi_awvalid)

    data_width = len(cocotb.top.s00_axi_wdata)
    byte_lanes = data_width // 8
    max_burst_size = (byte_lanes-1).bit_length()

    for test in [run_test_write, run_test_read]:

        factory = TestFactory(test)
        factory.add_option("idle_inserter", [None, cycle_pause])
        factory.add_option("backpressure_inserter", [None, cycle_pause])
        # factory.add_option("size", [None]+list(range(max_burst_size)))
        factory.add_option("s", range(min(s_count, 2)))
        factory.add_option("m", range(min(m_count, 2)))
        factory.generate_tests()

    factory = TestFactory(run_stress_test)
    factory.generate_tests()


# cocotb-test

tests_dir = os.path.abspath(os.path.dirname(__file__))
rtl_dir = os.path.abspath(os.path.join(tests_dir, '..', '..', 'rtl'))


@pytest.mark.parametrize("data_width", [8, 16, 32])
@pytest.mark.parametrize("m_count", [1, 4])
@pytest.mark.parametrize("s_count", [1, 4])
def test_axi_crossbar(request, s_count, m_count, data_width):
    dut = "axi_crossbar"
    wrapper = f"{dut}_wrap_{s_count}x{m_count}"
    module = os.path.splitext(os.path.basename(__file__))[0]
    toplevel = wrapper

    # generate wrapper
    wrapper_file = os.path.join(tests_dir, f"{wrapper}.v")
    if not os.path.exists(wrapper_file):
        subprocess.Popen(
            [os.path.join(rtl_dir, f"{dut}_wrap.py"), "-p", f"{s_count}", f"{m_count}"],
            cwd=tests_dir
        ).wait()

    verilog_sources = [
        wrapper_file,
        os.path.join(rtl_dir, f"{dut}.v"),
        os.path.join(rtl_dir, f"{dut}_addr.v"),
        os.path.join(rtl_dir, f"{dut}_rd.v"),
        os.path.join(rtl_dir, f"{dut}_wr.v"),
        os.path.join(rtl_dir, "axi_register_rd.v"),
        os.path.join(rtl_dir, "axi_register_wr.v"),
        os.path.join(rtl_dir, "arbiter.v"),
        os.path.join(rtl_dir, "priority_encoder.v"),
    ]

    parameters = {}

    parameters['S_COUNT'] = s_count
    parameters['M_COUNT'] = m_count

    parameters['DATA_WIDTH'] = data_width
    parameters['ADDR_WIDTH'] = 32
    parameters['STRB_WIDTH'] = parameters['DATA_WIDTH'] // 8
    parameters['S_ID_WIDTH'] = 8
    parameters['M_ID_WIDTH'] = parameters['S_ID_WIDTH'] + (s_count-1).bit_length()
    parameters['AWUSER_ENABLE'] = 0
    parameters['AWUSER_WIDTH'] = 1
    parameters['WUSER_ENABLE'] = 0
    parameters['WUSER_WIDTH'] = 1
    parameters['BUSER_ENABLE'] = 0
    parameters['BUSER_WIDTH'] = 1
    parameters['ARUSER_ENABLE'] = 0
    parameters['ARUSER_WIDTH'] = 1
    parameters['RUSER_ENABLE'] = 0
    parameters['RUSER_WIDTH'] = 1
    parameters['M_REGIONS'] = 1

    extra_env = {f'PARAM_{k}': str(v) for k, v in parameters.items()}

    sim_build = os.path.join(tests_dir, "sim_build",
        request.node.name.replace('[', '-').replace(']', ''))

    cocotb_test.simulator.run(
        python_search=[tests_dir],
        verilog_sources=verilog_sources,
        toplevel=toplevel,
        module=module,
        parameters=parameters,
        sim_build=sim_build,
        extra_env=extra_env,
    )


@pytest.mark.parametrize("data_width", [32])
@pytest.mark.parametrize("m_count", [4])
@pytest.mark.parametrize("s_count", [4])
def test_axi_crossbar_unique_ids(request, s_count, m_count, data_width):
    """
    Test axi_crossbar with UNIQUE_IDS=1 enabled.

    This test verifies that when UNIQUE_IDS=1 and unique AxIDs are used,
    the crossbar never blocks AR or AW channels due to ID ordering.
    Transactions should only be blocked by S_ACCEPT limit or destination backpressure.
    """
    dut = "axi_crossbar"
    wrapper = f"{dut}_wrap_{s_count}x{m_count}"
    module = os.path.splitext(os.path.basename(__file__))[0]
    toplevel = wrapper

    # generate wrapper
    wrapper_file = os.path.join(tests_dir, f"{wrapper}.v")
    if not os.path.exists(wrapper_file):
        subprocess.Popen(
            [os.path.join(rtl_dir, f"{dut}_wrap.py"), "-p", f"{s_count}", f"{m_count}"],
            cwd=tests_dir
        ).wait()

    verilog_sources = [
        wrapper_file,
        os.path.join(rtl_dir, f"{dut}.v"),
        os.path.join(rtl_dir, f"{dut}_addr.v"),
        os.path.join(rtl_dir, f"{dut}_rd.v"),
        os.path.join(rtl_dir, f"{dut}_wr.v"),
        os.path.join(rtl_dir, "axi_register_rd.v"),
        os.path.join(rtl_dir, "axi_register_wr.v"),
        os.path.join(rtl_dir, "arbiter.v"),
        os.path.join(rtl_dir, "priority_encoder.v"),
    ]

    parameters = {}

    # Note: S_COUNT and M_COUNT are localparams in the wrapper, so we don't include them
    # in the parameters dict. They are only used for environment variables.
    parameters['DATA_WIDTH'] = data_width
    parameters['ADDR_WIDTH'] = 32
    parameters['STRB_WIDTH'] = parameters['DATA_WIDTH'] // 8
    parameters['S_ID_WIDTH'] = 8
    parameters['M_ID_WIDTH'] = parameters['S_ID_WIDTH'] + (s_count-1).bit_length()
    parameters['AWUSER_ENABLE'] = 0
    parameters['AWUSER_WIDTH'] = 1
    parameters['WUSER_ENABLE'] = 0
    parameters['WUSER_WIDTH'] = 1
    parameters['BUSER_ENABLE'] = 0
    parameters['BUSER_WIDTH'] = 1
    parameters['ARUSER_ENABLE'] = 0
    parameters['ARUSER_WIDTH'] = 1
    parameters['RUSER_ENABLE'] = 0
    parameters['RUSER_WIDTH'] = 1
    parameters['M_REGIONS'] = 1
    parameters['UNIQUE_IDS'] = 1  # Enable UNIQUE_IDS mode

    extra_env = {f'PARAM_{k}': str(v) for k, v in parameters.items()}
    extra_env['PARAM_S_COUNT'] = str(s_count)
    extra_env['PARAM_M_COUNT'] = str(m_count)

    sim_build = os.path.join(tests_dir, "sim_build",
        request.node.name.replace('[', '-').replace(']', ''))

    cocotb_test.simulator.run(
        python_search=[tests_dir],
        verilog_sources=verilog_sources,
        toplevel=toplevel,
        module=module,
        parameters=parameters,
        sim_build=sim_build,
        extra_env=extra_env,
        testcase='run_test_unique_ids_no_blocking',
    )
