#!/usr/bin/python3

#
# This tool helps me to check Linux kernel options against
# my security hardening preferences for X86_64, ARM64, X86_32, and ARM.
# Let the computers do their job!
#
# Author: Alexander Popov <alex.popov@linux.com>
#
# Please don't cry if my Python code looks like C.
#
#
# N.B Hardening command line parameters:
#    slab_nomerge
#    page_alloc.shuffle=1
#    iommu=force (does it help against DMA attacks?)
#    slub_debug=FZ (slow)
#    init_on_alloc=1 (since v5.3)
#    init_on_free=1 (since v5.3, otherwise slub_debug=P and page_poison=1)
#    loadpin.enforce=1
#    debugfs=no-mount (or off if possible)
#    randomize_kstack_offset=1
#
#    Mitigations of CPU vulnerabilities:
#       Аrch-independent:
#           mitigations=auto,nosmt (nosmt is slow)
#       X86:
#           spectre_v2=on
#           pti=on
#           spec_store_bypass_disable=on
#           l1tf=full,force
#           l1d_flush=on (a part of the l1tf option)
#           mds=full,nosmt
#           tsx=off
#       ARM64:
#           kpti=on
#           ssbd=force-on
#
#    Should NOT be set:
#           nokaslr
#           arm64.nobti
#           arm64.nopauth
#
#    Hardware tag-based KASAN with arm64 Memory Tagging Extension (MTE):
#           kasan=on
#           kasan.stacktrace=off
#           kasan.fault=panic
#
# N.B. Hardening sysctls:
#    kernel.kptr_restrict=2 (or 1?)
#    kernel.dmesg_restrict=1 (also see the kconfig option)
#    kernel.perf_event_paranoid=3
#    kernel.kexec_load_disabled=1
#    kernel.yama.ptrace_scope=3
#    user.max_user_namespaces=0
#    what about bpf_jit_enable?
#    kernel.unprivileged_bpf_disabled=1
#    net.core.bpf_jit_harden=2
#
#    vm.unprivileged_userfaultfd=0
#        (at first, it disabled unprivileged userfaultfd,
#         and since v5.11 it enables unprivileged userfaultfd for user-mode only)
#
#    dev.tty.ldisc_autoload=0
#    fs.protected_symlinks=1
#    fs.protected_hardlinks=1
#    fs.protected_fifos=2
#    fs.protected_regular=2
#    fs.suid_dumpable=0
#    kernel.modules_disabled=1


# pylint: disable=missing-module-docstring,missing-class-docstring,missing-function-docstring
# pylint: disable=line-too-long,invalid-name,too-many-branches,too-many-statements


import sys
from argparse import ArgumentParser
from collections import OrderedDict
import re
import json
from .__about__ import __version__

TYPES_OF_CHECKS = ('kconfig', 'version')

class OptCheck:
    def __init__(self, reason, decision, name, expected):
        self.name = name
        self.expected = expected
        self.decision = decision
        self.reason = reason
        self.state = None
        self.result = None

    def check(self):
        if self.expected == self.state:
            self.result = 'OK'
        elif self.state is None:
            if self.expected == 'is not set':
                self.result = 'OK: not found'
            else:
                self.result = 'FAIL: not found'
        else:
            self.result = 'FAIL: "' + self.state + '"'

    def table_print(self, _mode, with_results):
        print('{:<40}|{:^7}|{:^12}|{:^10}|{:^18}'.format(self.name, self.type, self.expected, self.decision, self.reason), end='')
        if with_results:
            print('| {}'.format(self.result), end='')

    def json_dump(self, with_results):
        dump = [self.name, self.type, self.expected, self.decision, self.reason]
        if with_results:
            dump.append(self.result)
        return dump


class KconfigCheck(OptCheck):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = 'CONFIG_' + self.name

    @property
    def type(self):
        return 'kconfig'


class CmdlineCheck(OptCheck):
    @property
    def type(self):
        return 'cmdline'


class VersionCheck:
    def __init__(self, ver_expected):
        self.ver_expected = ver_expected
        self.ver = ()
        self.result = None

    @property
    def type(self):
        return 'version'

    def check(self):
        if self.ver[0] > self.ver_expected[0]:
            self.result = 'OK: version >= ' + str(self.ver_expected[0]) + '.' + str(self.ver_expected[1])
            return
        if self.ver[0] < self.ver_expected[0]:
            self.result = 'FAIL: version < ' + str(self.ver_expected[0]) + '.' + str(self.ver_expected[1])
            return
        if self.ver[1] >= self.ver_expected[1]:
            self.result = 'OK: version >= ' + str(self.ver_expected[0]) + '.' + str(self.ver_expected[1])
            return
        self.result = 'FAIL: version < ' + str(self.ver_expected[0]) + '.' + str(self.ver_expected[1])

    def table_print(self, _mode, with_results):
        ver_req = 'kernel version >= ' + str(self.ver_expected[0]) + '.' + str(self.ver_expected[1])
        print('{:<91}'.format(ver_req), end='')
        if with_results:
            print('| {}'.format(self.result), end='')


class PresenceCheck:
    def __init__(self, name, type):
        self.type = type
        if self.type == 'kconfig':
            self.name = 'CONFIG_' + name
        else:
            sys.exit('[!] ERROR: unsupported type "{}" for {}'.format(type, self.__class__.__name__))
        self.state = None
        self.result = None

    def check(self):
        if self.state is None:
            self.result = 'FAIL: not present'
            return
        self.result = 'OK: is present'

    def table_print(self, _mode, with_results):
        print('{:<91}'.format(self.name + ' is present'), end='')
        if with_results:
            print('| {}'.format(self.result), end='')


class ComplexOptCheck:
    def __init__(self, *opts):
        self.opts = opts
        if not self.opts:
            sys.exit('[!] ERROR: empty {} check'.format(self.__class__.__name__))
        if len(self.opts) == 1:
            sys.exit('[!] ERROR: useless {} check'.format(self.__class__.__name__))
        if not isinstance(opts[0], KconfigCheck) and not isinstance(opts[0], CmdlineCheck):
            sys.exit('[!] ERROR: invalid {} check: {}'.format(self.__class__.__name__, opts))
        self.result = None

    @property
    def name(self):
        return self.opts[0].name

    @property
    def type(self):
        return 'complex'

    @property
    def expected(self):
        return self.opts[0].expected

    @property
    def decision(self):
        return self.opts[0].decision

    @property
    def reason(self):
        return self.opts[0].reason

    def table_print(self, mode, with_results):
        if mode == 'verbose':
            print('    {:87}'.format('<<< ' + self.__class__.__name__ + ' >>>'), end='')
            if with_results:
                print('| {}'.format(self.result), end='')
            for o in self.opts:
                print()
                o.table_print(mode, with_results)
        else:
            o = self.opts[0]
            o.table_print(mode, False)
            if with_results:
                print('| {}'.format(self.result), end='')

    def json_dump(self, with_results):
        dump = self.opts[0].json_dump(False)
        if with_results:
            dump.append(self.result)
        return dump


class OR(ComplexOptCheck):
    # self.opts[0] is the option that this OR-check is about.
    # Use cases:
    #     OR(<X_is_hardened>, <X_is_disabled>)
    #     OR(<X_is_hardened>, <old_X_is_hardened>)
    def check(self):
        if not self.opts:
            sys.exit('[!] ERROR: invalid OR check')
        for i, opt in enumerate(self.opts):
            opt.check()
            if opt.result.startswith('OK'):
                if opt.result == 'OK' and i != 0:
                    # Simple OK is not enough for additional checks, add more info:
                    self.result = 'OK: {} "{}"'.format(opt.name, opt.expected)
                else:
                    self.result = opt.result
                return
        self.result = self.opts[0].result


class AND(ComplexOptCheck):
    # self.opts[0] is the option that this AND-check is about.
    # Use cases:
    #     AND(<suboption>, <main_option>)
    #       Suboption is not checked if checking of the main_option is failed.
    #     AND(<X_is_disabled>, <old_X_is_disabled>)
    def check(self):
        for i, opt in reversed(list(enumerate(self.opts))):
            opt.check()
            if i == 0:
                self.result = opt.result
                return
            if not opt.result.startswith('OK'):
                # This FAIL is caused by additional checks,
                # and not by the main option that this AND-check is about.
                # Describe the reason of the FAIL.
                if opt.result.startswith('FAIL: \"') or opt.result == 'FAIL: not found':
                    self.result = 'FAIL: {} not "{}"'.format(opt.name, opt.expected)
                elif opt.result == 'FAIL: not present':
                    self.result = 'FAIL: {} not present'.format(opt.name)
                else:
                    # This FAIL message is self-explaining.
                    self.result = opt.result
                return
        sys.exit('[!] ERROR: invalid AND check')


def detect_arch(fname, archs):
    with open(fname, 'r') as f:
        arch_pattern = re.compile("CONFIG_[a-zA-Z0-9_]*=y")
        arch = None
        for line in f.readlines():
            if arch_pattern.match(line):
                option, _ = line[7:].split('=', 1)
                if option in archs:
                    if not arch:
                        arch = option
                    else:
                        return None, 'more than one supported architecture is detected'
        if not arch:
            return None, 'failed to detect architecture'
        return arch, 'OK'


def detect_version(fname):
    with open(fname, 'r') as f:
        ver_pattern = re.compile("# Linux/.* Kernel Configuration")
        for line in f.readlines():
            if ver_pattern.match(line):
                line = line.strip()
                parts = line.split()
                ver_str = parts[2]
                ver_numbers = ver_str.split('.')
                if len(ver_numbers) < 3 or not ver_numbers[0].isdigit() or not ver_numbers[1].isdigit():
                    msg = 'failed to parse the version "' + ver_str + '"'
                    return None, msg
                return (int(ver_numbers[0]), int(ver_numbers[1])), None
        return None, 'no kernel version detected'


def add_kconfig_checks(l, arch):
    # Calling the KconfigCheck class constructor:
    #     KconfigCheck(reason, decision, name, expected)

    modules_not_set = KconfigCheck('cut_attack_surface', 'kspp', 'MODULES', 'is not set')
    devmem_not_set = KconfigCheck('cut_attack_surface', 'kspp', 'DEVMEM', 'is not set') # refers to LOCKDOWN
    efi_not_set = KconfigCheck('cut_attack_surface', 'my', 'EFI', 'is not set')

    # 'self_protection', 'defconfig'
    l += [KconfigCheck('self_protection', 'defconfig', 'BUG', 'y')]
    l += [KconfigCheck('self_protection', 'defconfig', 'SLUB_DEBUG', 'y')]
    l += [KconfigCheck('self_protection', 'defconfig', 'GCC_PLUGINS', 'y')]
    l += [OR(KconfigCheck('self_protection', 'defconfig', 'STACKPROTECTOR_STRONG', 'y'),
             KconfigCheck('self_protection', 'defconfig', 'CC_STACKPROTECTOR_STRONG', 'y'))]
    l += [OR(KconfigCheck('self_protection', 'defconfig', 'STRICT_KERNEL_RWX', 'y'),
             KconfigCheck('self_protection', 'defconfig', 'DEBUG_RODATA', 'y'))] # before v4.11
    l += [OR(KconfigCheck('self_protection', 'defconfig', 'STRICT_MODULE_RWX', 'y'),
             KconfigCheck('self_protection', 'defconfig', 'DEBUG_SET_MODULE_RONX', 'y'),
             modules_not_set)] # DEBUG_SET_MODULE_RONX was before v4.11
    l += [OR(KconfigCheck('self_protection', 'defconfig', 'REFCOUNT_FULL', 'y'),
             VersionCheck((5, 5)))] # REFCOUNT_FULL is enabled by default since v5.5
    l += [KconfigCheck('self_protection', 'defconfig', 'THREAD_INFO_IN_TASK', 'y')]
    iommu_support_is_set = KconfigCheck('self_protection', 'defconfig', 'IOMMU_SUPPORT', 'y')
    l += [iommu_support_is_set] # is needed for mitigating DMA attacks
    if arch in ('X86_64', 'ARM64', 'X86_32'):
        l += [KconfigCheck('self_protection', 'defconfig', 'RANDOMIZE_BASE', 'y')]
    if arch in ('X86_64', 'ARM64'):
        l += [KconfigCheck('self_protection', 'defconfig', 'VMAP_STACK', 'y')]
    if arch in ('X86_64', 'X86_32'):
        l += [KconfigCheck('self_protection', 'defconfig', 'MICROCODE', 'y')] # is needed for mitigating CPU bugs
        l += [KconfigCheck('self_protection', 'defconfig', 'RETPOLINE', 'y')]
        l += [KconfigCheck('self_protection', 'defconfig', 'X86_SMAP', 'y')]
        l += [KconfigCheck('self_protection', 'defconfig', 'SYN_COOKIES', 'y')] # another reason?
        l += [OR(KconfigCheck('self_protection', 'defconfig', 'X86_UMIP', 'y'),
                 KconfigCheck('self_protection', 'defconfig', 'X86_INTEL_UMIP', 'y'))]
    if arch in ('ARM64', 'ARM'):
        l += [KconfigCheck('self_protection', 'defconfig', 'STACKPROTECTOR_PER_TASK', 'y')]
    if arch == 'X86_64':
        l += [KconfigCheck('self_protection', 'defconfig', 'PAGE_TABLE_ISOLATION', 'y')]
        l += [KconfigCheck('self_protection', 'defconfig', 'RANDOMIZE_MEMORY', 'y')]
        l += [AND(KconfigCheck('self_protection', 'defconfig', 'INTEL_IOMMU', 'y'),
                  iommu_support_is_set)]
        l += [AND(KconfigCheck('self_protection', 'defconfig', 'AMD_IOMMU', 'y'),
                  iommu_support_is_set)]
    if arch == 'ARM64':
        l += [KconfigCheck('self_protection', 'defconfig', 'ARM64_PAN', 'y')]
        l += [KconfigCheck('self_protection', 'defconfig', 'ARM64_EPAN', 'y')]
        l += [KconfigCheck('self_protection', 'defconfig', 'UNMAP_KERNEL_AT_EL0', 'y')]
        l += [OR(KconfigCheck('self_protection', 'defconfig', 'HARDEN_EL2_VECTORS', 'y'),
                 AND(KconfigCheck('self_protection', 'defconfig', 'RANDOMIZE_BASE', 'y'),
                     VersionCheck((5, 9))))] # HARDEN_EL2_VECTORS was included in RANDOMIZE_BASE in v5.9
        l += [KconfigCheck('self_protection', 'defconfig', 'RODATA_FULL_DEFAULT_ENABLED', 'y')]
        l += [KconfigCheck('self_protection', 'defconfig', 'ARM64_PTR_AUTH_KERNEL', 'y')]
        l += [KconfigCheck('self_protection', 'defconfig', 'ARM64_BTI_KERNEL', 'y')]
        l += [OR(KconfigCheck('self_protection', 'defconfig', 'HARDEN_BRANCH_PREDICTOR', 'y'),
                 VersionCheck((5, 10)))] # HARDEN_BRANCH_PREDICTOR is enabled by default since v5.10
        l += [KconfigCheck('self_protection', 'defconfig', 'MITIGATE_SPECTRE_BRANCH_HISTORY', 'y')]
        l += [KconfigCheck('self_protection', 'defconfig', 'ARM64_MTE', 'y')]
    if arch == 'ARM':
        l += [KconfigCheck('self_protection', 'defconfig', 'CPU_SW_DOMAIN_PAN', 'y')]
        l += [KconfigCheck('self_protection', 'defconfig', 'HARDEN_BRANCH_PREDICTOR', 'y')]
        l += [KconfigCheck('self_protection', 'defconfig', 'HARDEN_BRANCH_HISTORY', 'y')]

    # 'self_protection', 'kspp'
    l += [KconfigCheck('self_protection', 'kspp', 'SECURITY_DMESG_RESTRICT', 'y')]
    l += [KconfigCheck('self_protection', 'kspp', 'BUG_ON_DATA_CORRUPTION', 'y')]
    l += [KconfigCheck('self_protection', 'kspp', 'DEBUG_WX', 'y')]
    l += [KconfigCheck('self_protection', 'kspp', 'SCHED_STACK_END_CHECK', 'y')]
    l += [KconfigCheck('self_protection', 'kspp', 'SLAB_FREELIST_HARDENED', 'y')]
    l += [KconfigCheck('self_protection', 'kspp', 'SLAB_FREELIST_RANDOM', 'y')]
    l += [KconfigCheck('self_protection', 'kspp', 'SHUFFLE_PAGE_ALLOCATOR', 'y')]
    l += [KconfigCheck('self_protection', 'kspp', 'FORTIFY_SOURCE', 'y')]
    l += [KconfigCheck('self_protection', 'kspp', 'DEBUG_LIST', 'y')]
    l += [KconfigCheck('self_protection', 'kspp', 'DEBUG_SG', 'y')]
    l += [KconfigCheck('self_protection', 'kspp', 'DEBUG_CREDENTIALS', 'y')]
    l += [KconfigCheck('self_protection', 'kspp', 'DEBUG_NOTIFIERS', 'y')]
    l += [KconfigCheck('self_protection', 'kspp', 'INIT_ON_ALLOC_DEFAULT_ON', 'y')]
    l += [KconfigCheck('self_protection', 'kspp', 'GCC_PLUGIN_LATENT_ENTROPY', 'y')]
    randstruct_is_set = KconfigCheck('self_protection', 'kspp', 'GCC_PLUGIN_RANDSTRUCT', 'y')
    l += [randstruct_is_set]
    hardened_usercopy_is_set = KconfigCheck('self_protection', 'kspp', 'HARDENED_USERCOPY', 'y')
    l += [hardened_usercopy_is_set]
    l += [AND(KconfigCheck('self_protection', 'kspp', 'HARDENED_USERCOPY_FALLBACK', 'is not set'),
              hardened_usercopy_is_set)]
    l += [AND(KconfigCheck('self_protection', 'kspp', 'HARDENED_USERCOPY_PAGESPAN', 'is not set'),
              hardened_usercopy_is_set)]
    l += [OR(KconfigCheck('self_protection', 'kspp', 'MODULE_SIG', 'y'),
             modules_not_set)]
    l += [OR(KconfigCheck('self_protection', 'kspp', 'MODULE_SIG_ALL', 'y'),
             modules_not_set)]
    l += [OR(KconfigCheck('self_protection', 'kspp', 'MODULE_SIG_SHA512', 'y'),
             modules_not_set)]
    l += [OR(KconfigCheck('self_protection', 'kspp', 'MODULE_SIG_FORCE', 'y'),
             modules_not_set)] # refers to LOCKDOWN
    l += [OR(KconfigCheck('self_protection', 'kspp', 'INIT_STACK_ALL_ZERO', 'y'),
             KconfigCheck('self_protection', 'kspp', 'GCC_PLUGIN_STRUCTLEAK_BYREF_ALL', 'y'))]
    l += [OR(KconfigCheck('self_protection', 'kspp', 'INIT_ON_FREE_DEFAULT_ON', 'y'),
             KconfigCheck('self_protection', 'kspp', 'PAGE_POISONING_ZERO', 'y'))]
             # CONFIG_INIT_ON_FREE_DEFAULT_ON was added in v5.3.
             # CONFIG_PAGE_POISONING_ZERO was removed in v5.11.
             # Starting from v5.11 CONFIG_PAGE_POISONING unconditionally checks
             # the 0xAA poison pattern on allocation.
             # That brings higher performance penalty.
    if arch in ('X86_64', 'ARM64', 'X86_32'):
        stackleak_is_set = KconfigCheck('self_protection', 'kspp', 'GCC_PLUGIN_STACKLEAK', 'y')
        l += [stackleak_is_set]
        l += [KconfigCheck('self_protection', 'kspp', 'RANDOMIZE_KSTACK_OFFSET_DEFAULT', 'y')]
    if arch in ('X86_64', 'X86_32'):
        l += [KconfigCheck('self_protection', 'kspp', 'DEFAULT_MMAP_MIN_ADDR', '65536')]
    if arch in ('ARM64', 'ARM'):
        l += [KconfigCheck('self_protection', 'kspp', 'DEFAULT_MMAP_MIN_ADDR', '32768')]
        l += [KconfigCheck('self_protection', 'kspp', 'SYN_COOKIES', 'y')] # another reason?
    if arch == 'ARM64':
        l += [KconfigCheck('self_protection', 'kspp', 'ARM64_SW_TTBR0_PAN', 'y')]
    if arch == 'X86_32':
        l += [KconfigCheck('self_protection', 'kspp', 'PAGE_TABLE_ISOLATION', 'y')]
        l += [KconfigCheck('self_protection', 'kspp', 'HIGHMEM64G', 'y')]
        l += [KconfigCheck('self_protection', 'kspp', 'X86_PAE', 'y')]

    # 'self_protection', 'maintainer'
    ubsan_bounds_is_set = KconfigCheck('self_protection', 'maintainer', 'UBSAN_BOUNDS', 'y') # only array index bounds checking
    l += [ubsan_bounds_is_set] # recommended by Kees Cook in /issues/53
    l += [AND(KconfigCheck('self_protection', 'maintainer', 'UBSAN_SANITIZE_ALL', 'y'),
              ubsan_bounds_is_set)] # recommended by Kees Cook in /issues/53
    l += [AND(KconfigCheck('self_protection', 'maintainer', 'UBSAN_TRAP', 'y'),
              ubsan_bounds_is_set)] # recommended by Kees Cook in /issues/53

    # 'self_protection', 'clipos'
    l += [KconfigCheck('self_protection', 'clipos', 'DEBUG_VIRTUAL', 'y')]
    l += [KconfigCheck('self_protection', 'clipos', 'STATIC_USERMODEHELPER', 'y')] # needs userspace support
    l += [OR(KconfigCheck('self_protection', 'clipos', 'EFI_DISABLE_PCI_DMA', 'y'),
             efi_not_set)]
    l += [KconfigCheck('self_protection', 'clipos', 'SLAB_MERGE_DEFAULT', 'is not set')] # slab_nomerge
    l += [KconfigCheck('self_protection', 'clipos', 'RANDOM_TRUST_BOOTLOADER', 'is not set')]
    l += [KconfigCheck('self_protection', 'clipos', 'RANDOM_TRUST_CPU', 'is not set')]
    l += [AND(KconfigCheck('self_protection', 'clipos', 'GCC_PLUGIN_RANDSTRUCT_PERFORMANCE', 'is not set'),
              randstruct_is_set)]
    if arch in ('X86_64', 'ARM64', 'X86_32'):
        l += [AND(KconfigCheck('self_protection', 'clipos', 'STACKLEAK_METRICS', 'is not set'),
                  stackleak_is_set)]
        l += [AND(KconfigCheck('self_protection', 'clipos', 'STACKLEAK_RUNTIME_DISABLE', 'is not set'),
                  stackleak_is_set)]
    if arch in ('X86_64', 'X86_32'):
        l += [AND(KconfigCheck('self_protection', 'clipos', 'INTEL_IOMMU_DEFAULT_ON', 'y'),
                  iommu_support_is_set)]
    if arch == 'X86_64':
        l += [AND(KconfigCheck('self_protection', 'clipos', 'INTEL_IOMMU_SVM', 'y'),
                  iommu_support_is_set)]
    if arch == 'X86_32':
        l += [AND(KconfigCheck('self_protection', 'clipos', 'INTEL_IOMMU', 'y'),
                  iommu_support_is_set)]

    # 'self_protection', 'my'
    l += [OR(KconfigCheck('self_protection', 'my', 'RESET_ATTACK_MITIGATION', 'y'),
             efi_not_set)] # needs userspace support (systemd)
    if arch == 'X86_64':
        l += [KconfigCheck('self_protection', 'my', 'SLS', 'y')] # vs CVE-2021-26341 in Straight-Line-Speculation
        l += [AND(KconfigCheck('self_protection', 'my', 'AMD_IOMMU_V2', 'y'),
                  iommu_support_is_set)]
    if arch == 'ARM64':
        l += [KconfigCheck('self_protection', 'my', 'SHADOW_CALL_STACK', 'y')] # depends on clang, maybe it's alternative to STACKPROTECTOR_STRONG
        l += [KconfigCheck('self_protection', 'my', 'KASAN_HW_TAGS', 'y')]
        cfi_clang_is_set = KconfigCheck('self_protection', 'my', 'CFI_CLANG', 'y')
        l += [cfi_clang_is_set]
        l += [AND(KconfigCheck('self_protection', 'my', 'CFI_PERMISSIVE', 'is not set'),
                  cfi_clang_is_set)]

    # 'security_policy'
    if arch in ('X86_64', 'ARM64', 'X86_32'):
        l += [KconfigCheck('security_policy', 'defconfig', 'SECURITY', 'y')] # and choose your favourite LSM
    if arch == 'ARM':
        l += [KconfigCheck('security_policy', 'kspp', 'SECURITY', 'y')] # and choose your favourite LSM
    l += [KconfigCheck('security_policy', 'kspp', 'SECURITY_YAMA', 'y')]
    l += [OR(KconfigCheck('security_policy', 'my', 'SECURITY_WRITABLE_HOOKS', 'is not set'),
             KconfigCheck('security_policy', 'kspp', 'SECURITY_SELINUX_DISABLE', 'is not set'))]
    l += [KconfigCheck('security_policy', 'clipos', 'SECURITY_LOCKDOWN_LSM', 'y')]
    l += [KconfigCheck('security_policy', 'clipos', 'SECURITY_LOCKDOWN_LSM_EARLY', 'y')]
    l += [KconfigCheck('security_policy', 'clipos', 'LOCK_DOWN_KERNEL_FORCE_CONFIDENTIALITY', 'y')]
    l += [KconfigCheck('security_policy', 'my', 'SECURITY_SAFESETID', 'y')]
    loadpin_is_set = KconfigCheck('security_policy', 'my', 'SECURITY_LOADPIN', 'y')
    l += [loadpin_is_set] # needs userspace support
    l += [AND(KconfigCheck('security_policy', 'my', 'SECURITY_LOADPIN_ENFORCE', 'y'),
              loadpin_is_set)]

    # 'cut_attack_surface', 'defconfig'
    l += [KconfigCheck('cut_attack_surface', 'defconfig', 'BPF_UNPRIV_DEFAULT_OFF', 'y')] # see unprivileged_bpf_disabled
    l += [KconfigCheck('cut_attack_surface', 'defconfig', 'SECCOMP', 'y')]
    l += [KconfigCheck('cut_attack_surface', 'defconfig', 'SECCOMP_FILTER', 'y')]
    if arch in ('X86_64', 'ARM64', 'X86_32'):
        l += [OR(KconfigCheck('cut_attack_surface', 'defconfig', 'STRICT_DEVMEM', 'y'),
                 devmem_not_set)] # refers to LOCKDOWN

    # 'cut_attack_surface', 'kspp'
    l += [KconfigCheck('cut_attack_surface', 'kspp', 'ACPI_CUSTOM_METHOD', 'is not set')] # refers to LOCKDOWN
    l += [KconfigCheck('cut_attack_surface', 'kspp', 'COMPAT_BRK', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'kspp', 'DEVKMEM', 'is not set')] # refers to LOCKDOWN
    l += [KconfigCheck('cut_attack_surface', 'kspp', 'COMPAT_VDSO', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'kspp', 'BINFMT_MISC', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'kspp', 'INET_DIAG', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'kspp', 'KEXEC', 'is not set')] # refers to LOCKDOWN
    l += [KconfigCheck('cut_attack_surface', 'kspp', 'PROC_KCORE', 'is not set')] # refers to LOCKDOWN
    l += [KconfigCheck('cut_attack_surface', 'kspp', 'LEGACY_PTYS', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'kspp', 'HIBERNATION', 'is not set')] # refers to LOCKDOWN
    l += [KconfigCheck('cut_attack_surface', 'kspp', 'IA32_EMULATION', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'kspp', 'X86_X32', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'kspp', 'MODIFY_LDT_SYSCALL', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'kspp', 'OABI_COMPAT', 'is not set')]
    l += [modules_not_set]
    l += [devmem_not_set]
    l += [OR(KconfigCheck('cut_attack_surface', 'kspp', 'IO_STRICT_DEVMEM', 'y'),
             devmem_not_set)] # refers to LOCKDOWN
    if arch == 'ARM':
        l += [OR(KconfigCheck('cut_attack_surface', 'kspp', 'STRICT_DEVMEM', 'y'),
                 devmem_not_set)] # refers to LOCKDOWN
    if arch == 'X86_64':
        l += [KconfigCheck('cut_attack_surface', 'kspp', 'LEGACY_VSYSCALL_NONE', 'y')] # 'vsyscall=none'

    # 'cut_attack_surface', 'grsec'
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'ZSMALLOC_STAT', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'PAGE_OWNER', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'DEBUG_KMEMLEAK', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'BINFMT_AOUT', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'KPROBE_EVENTS', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'UPROBE_EVENTS', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'GENERIC_TRACER', 'is not set')] # refers to LOCKDOWN
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'FUNCTION_TRACER', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'STACK_TRACER', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'HIST_TRIGGERS', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'BLK_DEV_IO_TRACE', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'PROC_VMCORE', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'PROC_PAGE_MONITOR', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'USELIB', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'CHECKPOINT_RESTORE', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'USERFAULTFD', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'HWPOISON_INJECT', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'MEM_SOFT_DIRTY', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'DEVPORT', 'is not set')] # refers to LOCKDOWN
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'DEBUG_FS', 'is not set')] # refers to LOCKDOWN
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'NOTIFIER_ERROR_INJECTION', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'FAIL_FUTEX', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'PUNIT_ATOM_DEBUG', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'ACPI_CONFIGFS', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'EDAC_DEBUG', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'DRM_I915_DEBUG', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'BCACHE_CLOSURES_DEBUG', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'DVB_C8SECTPFE', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'MTD_SLRAM', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'MTD_PHRAM', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'IO_URING', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'KCMP', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'RSEQ', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'LATENCYTOP', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'KCOV', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'PROVIDE_OHCI1394_DMA_INIT', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'grsec', 'SUNRPC_DEBUG', 'is not set')]
    l += [AND(KconfigCheck('cut_attack_surface', 'grsec', 'PTDUMP_DEBUGFS', 'is not set'),
              KconfigCheck('cut_attack_surface', 'grsec', 'X86_PTDUMP', 'is not set'))]

    # 'cut_attack_surface', 'maintainer'
    l += [KconfigCheck('cut_attack_surface', 'maintainer', 'DRM_LEGACY', 'is not set')] # recommended by Daniel Vetter in /issues/38
    l += [KconfigCheck('cut_attack_surface', 'maintainer', 'FB', 'is not set')] # recommended by Daniel Vetter in /issues/38
    l += [KconfigCheck('cut_attack_surface', 'maintainer', 'VT', 'is not set')] # recommended by Daniel Vetter in /issues/38
    l += [KconfigCheck('cut_attack_surface', 'maintainer', 'BLK_DEV_FD', 'is not set')] # recommended by Denis Efremov in /pull/54

    # 'cut_attack_surface', 'grapheneos'
    l += [KconfigCheck('cut_attack_surface', 'grapheneos', 'AIO', 'is not set')]

    # 'cut_attack_surface', 'clipos'
    l += [KconfigCheck('cut_attack_surface', 'clipos', 'STAGING', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'clipos', 'KSM', 'is not set')] # to prevent FLUSH+RELOAD attack
#   l += [KconfigCheck('cut_attack_surface', 'clipos', 'IKCONFIG', 'is not set')] # no, IKCONFIG is needed for this check :)
    l += [KconfigCheck('cut_attack_surface', 'clipos', 'KALLSYMS', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'clipos', 'X86_VSYSCALL_EMULATION', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'clipos', 'MAGIC_SYSRQ', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'clipos', 'KEXEC_FILE', 'is not set')] # refers to LOCKDOWN (permissive)
    l += [KconfigCheck('cut_attack_surface', 'clipos', 'USER_NS', 'is not set')] # user.max_user_namespaces=0
    l += [KconfigCheck('cut_attack_surface', 'clipos', 'X86_MSR', 'is not set')] # refers to LOCKDOWN
    l += [KconfigCheck('cut_attack_surface', 'clipos', 'X86_CPUID', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'clipos', 'X86_IOPL_IOPERM', 'is not set')] # refers to LOCKDOWN
    l += [KconfigCheck('cut_attack_surface', 'clipos', 'ACPI_TABLE_UPGRADE', 'is not set')] # refers to LOCKDOWN
    l += [KconfigCheck('cut_attack_surface', 'clipos', 'EFI_CUSTOM_SSDT_OVERLAYS', 'is not set')]
    l += [AND(KconfigCheck('cut_attack_surface', 'clipos', 'LDISC_AUTOLOAD', 'is not set'),
              PresenceCheck('LDISC_AUTOLOAD', 'kconfig'))]
    if arch in ('X86_64', 'X86_32'):
        l += [KconfigCheck('cut_attack_surface', 'clipos', 'X86_INTEL_TSX_MODE_OFF', 'y')] # tsx=off

    # 'cut_attack_surface', 'lockdown'
    l += [KconfigCheck('cut_attack_surface', 'lockdown', 'EFI_TEST', 'is not set')] # refers to LOCKDOWN
    l += [KconfigCheck('cut_attack_surface', 'lockdown', 'BPF_SYSCALL', 'is not set')] # refers to LOCKDOWN
    l += [KconfigCheck('cut_attack_surface', 'lockdown', 'MMIOTRACE_TEST', 'is not set')] # refers to LOCKDOWN
    l += [KconfigCheck('cut_attack_surface', 'lockdown', 'KPROBES', 'is not set')] # refers to LOCKDOWN

    # 'cut_attack_surface', 'my'
    l += [OR(KconfigCheck('cut_attack_surface', 'my', 'TRIM_UNUSED_KSYMS', 'y'),
             modules_not_set)]
    l += [KconfigCheck('cut_attack_surface', 'my', 'MMIOTRACE', 'is not set')] # refers to LOCKDOWN (permissive)
    l += [KconfigCheck('cut_attack_surface', 'my', 'LIVEPATCH', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'my', 'IP_DCCP', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'my', 'IP_SCTP', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'my', 'FTRACE', 'is not set')] # refers to LOCKDOWN
    l += [KconfigCheck('cut_attack_surface', 'my', 'VIDEO_VIVID', 'is not set')]
    l += [KconfigCheck('cut_attack_surface', 'my', 'INPUT_EVBUG', 'is not set')] # Can be used as a keylogger

    # 'harden_userspace'
    if arch in ('X86_64', 'ARM64', 'X86_32'):
        l += [KconfigCheck('harden_userspace', 'defconfig', 'INTEGRITY', 'y')]
    if arch == 'ARM':
        l += [KconfigCheck('harden_userspace', 'my', 'INTEGRITY', 'y')]
    if arch == 'ARM64':
        l += [KconfigCheck('harden_userspace', 'defconfig', 'ARM64_MTE', 'y')]
    if arch in ('ARM', 'X86_32'):
        l += [KconfigCheck('harden_userspace', 'defconfig', 'VMSPLIT_3G', 'y')]
    if arch in ('X86_64', 'ARM64'):
        l += [KconfigCheck('harden_userspace', 'clipos', 'ARCH_MMAP_RND_BITS', '32')]
    if arch in ('X86_32', 'ARM'):
        l += [KconfigCheck('harden_userspace', 'my', 'ARCH_MMAP_RND_BITS', '16')]

#   l += [KconfigCheck('feature_test', 'my', 'LKDTM', 'm')] # only for debugging!


def add_cmdline_checks(l, arch):
    # Calling the CmdlineCheck class constructor:
    #     CmdlineCheck(reason, decision, name, expected)

    l += [CmdlineCheck('self_protection', 'kspp', 'randomize_kstack_offset', 'on')]
    # TODO: add other


def print_unknown_options(checklist, parsed_options):
    known_options = []

    for o1 in checklist:
        if o1.type != 'complex':
            known_options.append(o1.name)
            continue
        for o2 in o1.opts:
            if o2.type != 'complex':
                if hasattr(o2, 'name'):
                    known_options.append(o2.name)
                continue
            for o3 in o2.opts:
                if o3.type == 'complex':
                    sys.exit('[!] ERROR: unexpected ComplexOptCheck inside {}'.format(o2.name))
                if hasattr(o3, 'name'):
                    known_options.append(o3.name)

    for option, value in parsed_options.items():
        if option not in known_options:
            print('[?] No check for option {} ({})'.format(option, value))


def print_checklist(mode, checklist, with_results):
    if mode == 'json':
        output = []
        for o in checklist:
            output.append(o.json_dump(with_results))
        print(json.dumps(output))
        return

    # table header
    sep_line_len = 91
    if with_results:
        sep_line_len += 30
    print('=' * sep_line_len)
    print('{:^40}|{:^7}|{:^12}|{:^10}|{:^18}'.format('option name', 'type', 'desired val', 'decision', 'reason'), end='')
    if with_results:
        print('| {}'.format('check result'), end='')
    print()
    print('=' * sep_line_len)

    # table contents
    for opt in checklist:
        if with_results:
            if mode == 'show_ok':
                if not opt.result.startswith('OK'):
                    continue
            if mode == 'show_fail':
                if not opt.result.startswith('FAIL'):
                    continue
        opt.table_print(mode, with_results)
        print()
        if mode == 'verbose':
            print('-' * sep_line_len)
    print()

    # final score
    if with_results:
        fail_count = len(list(filter(lambda opt: opt.result.startswith('FAIL'), checklist)))
        fail_suppressed = ''
        ok_count = len(list(filter(lambda opt: opt.result.startswith('OK'), checklist)))
        ok_suppressed = ''
        if mode == 'show_ok':
            fail_suppressed = ' (suppressed in output)'
        if mode == 'show_fail':
            ok_suppressed = ' (suppressed in output)'
        if mode != 'json':
            print('[+] Config check is finished: \'OK\' - {}{} / \'FAIL\' - {}{}'.format(ok_count, ok_suppressed, fail_count, fail_suppressed))


def populate_simple_opt_with_data(opt, data, data_type):
    if opt.type == 'complex':
        sys.exit('[!] ERROR: unexpected ComplexOptCheck {}: {}'.format(opt.name, vars(opt)))
    if data_type not in TYPES_OF_CHECKS:
        sys.exit('[!] ERROR: invalid data type "{}"'.format(data_type))

    if data_type != opt.type:
        return

    if data_type == 'kconfig':
        opt.state = data.get(opt.name, None)
    elif data_type == 'version':
        opt.ver = data


def populate_opt_with_data(opt, data, data_type):
    if opt.type == 'complex':
        for o in opt.opts:
            if o.type == 'complex':
                # Recursion for nested ComplexOptCheck objects
                populate_opt_with_data(o, data, data_type)
            else:
                populate_simple_opt_with_data(o, data, data_type)
    else:
        if opt.type != 'kconfig':
            sys.exit('[!] ERROR: bad type "{}" for a simple check {}'.format(opt.type, opt.name))
        populate_simple_opt_with_data(opt, data, data_type)


def populate_with_data(checklist, data, data_type):
    for opt in checklist:
        populate_opt_with_data(opt, data, data_type)


def perform_checks(checklist):
    for opt in checklist:
        opt.check()


def parse_kconfig_file(parsed_options, fname):
    with open(fname, 'r') as f:
        opt_is_on = re.compile("CONFIG_[a-zA-Z0-9_]*=[a-zA-Z0-9_\"]*")
        opt_is_off = re.compile("# CONFIG_[a-zA-Z0-9_]* is not set")

        for line in f.readlines():
            line = line.strip()
            option = None
            value = None

            if opt_is_on.match(line):
                option, value = line.split('=', 1)
            elif opt_is_off.match(line):
                option, value = line[2:].split(' ', 1)
                if value != 'is not set':
                    sys.exit('[!] ERROR: bad disabled kconfig option "{}"'.format(line))

            if option in parsed_options:
                sys.exit('[!] ERROR: kconfig option "{}" exists multiple times'.format(line))

            if option:
                parsed_options[option] = value


def main():
    # Report modes:
    #   * verbose mode for
    #     - reporting about unknown kernel options in the kconfig
    #     - verbose printing of ComplexOptCheck items
    #   * json mode for printing the results in JSON format
    report_modes = ['verbose', 'json', 'show_ok', 'show_fail']
    supported_archs = ['X86_64', 'X86_32', 'ARM64', 'ARM']
    parser = ArgumentParser(prog='kconfig-hardened-check',
                            description='A tool for checking the security hardening options of the Linux kernel')
    parser.add_argument('--version', action='version', version='%(prog)s ' + __version__)
    parser.add_argument('-p', '--print', choices=supported_archs,
                        help='print security hardening preferences for the selected architecture')
    parser.add_argument('-c', '--config',
                        help='check the kernel kconfig file against these preferences')
    parser.add_argument('-m', '--mode', choices=report_modes,
                        help='choose the report mode')
    args = parser.parse_args()

    mode = None
    if args.mode:
        mode = args.mode
        if mode != 'json':
            print('[+] Special report mode: {}'.format(mode))

    config_checklist = []

    if args.config:
        if mode != 'json':
            print('[+] Kconfig file to check: {}'.format(args.config))

        arch, msg = detect_arch(args.config, supported_archs)
        if not arch:
            sys.exit('[!] ERROR: {}'.format(msg))
        if mode != 'json':
            print('[+] Detected architecture: {}'.format(arch))

        kernel_version, msg = detect_version(args.config)
        if not kernel_version:
            sys.exit('[!] ERROR: {}'.format(msg))
        if mode != 'json':
            print('[+] Detected kernel version: {}.{}'.format(kernel_version[0], kernel_version[1]))

        # add relevant kconfig checks to the checklist
        add_kconfig_checks(config_checklist, arch)

        # populate the checklist with the parsed kconfig data
        parsed_kconfig_options = OrderedDict()
        parse_kconfig_file(parsed_kconfig_options, args.config)
        populate_with_data(config_checklist, parsed_kconfig_options, 'kconfig')
        populate_with_data(config_checklist, kernel_version, 'version')

        # now everything is ready for performing the checks
        perform_checks(config_checklist)

        # finally print the results
        if mode == 'verbose':
            print_unknown_options(config_checklist, parsed_kconfig_options)
        print_checklist(mode, config_checklist, True)

        sys.exit(0)

    if args.print:
        if mode in ('show_ok', 'show_fail'):
            sys.exit('[!] ERROR: wrong mode "{}" for --print'.format(mode))
        arch = args.print
        add_kconfig_checks(config_checklist, arch)
        add_cmdline_checks(config_checklist, arch)
        if mode != 'json':
            print('[+] Printing kernel security hardening preferences for {}...'.format(arch))
        print_checklist(mode, config_checklist, False)
        sys.exit(0)

    parser.print_help()
    sys.exit(0)

if __name__ == '__main__':
    main()
