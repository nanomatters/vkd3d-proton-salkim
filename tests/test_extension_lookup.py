#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Test production extension lookup without a Vulkan device or Wine runtime.

The lookup, disable-list parser and native environment reader are extracted
unchanged from production. Environment queries and warning calls are counted.
--benchmark compares with --baseline (a pre-change device.c) using libc getenv,
not Wine's Windows environment implementation. It is a CPU microbenchmark, not
a device-creation, GPU, frame-rate or loading-time measurement.
"""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def functions(source, name):
    pattern = r"^(?:static )?bool " + name + r"\([^;]*?\)\n\{"
    result = []
    for match in re.finditer(pattern, source, re.M):
        end = source.index("{", match.start()) + 1
        depth = 1
        while depth:
            depth += (source[end] == "{") - (source[end] == "}")
            end += 1
        result.append(source[match.start():end])
    if not result:
        raise ValueError("Production function not found: " + name)
    return result


def generate(source, baseline):
    device = source.read_text()
    debug = (ROOT / "libs/vkd3d-common/debug.c").read_text()
    platform = (ROOT / "libs/vkd3d-common/platform.c").read_text()
    pieces = [PREFIX]
    pieces.append(functions(platform, "vkd3d_get_env_var")[-1].replace(
        "vkd3d_get_env_var", "platform_get_env_var"))
    pieces.append(ENV_WRAPPER)
    pieces.append(functions(debug, "is_option_separator")[0])
    pieces.append(functions(debug, "vkd3d_debug_list_has_member")[0])
    pieces.append(functions(device, "is_extension_disabled")[0])
    pieces.append(functions(device, "has_extension")[0])
    if baseline:
        previous = baseline.read_text()
        assert functions(previous, "is_extension_disabled")[0] == functions(
            device, "is_extension_disabled")[0]
        pieces.append("#define HAVE_BASELINE 1")
        pieces.append(functions(previous, "has_extension")[0].replace(
            "has_extension", "baseline_has_extension"))
    pieces.append(TESTS)
    if baseline:
        pieces.append(BENCHMARK)
    pieces.append(MAIN.replace("@BENCHMARK@", "benchmark();" if baseline else ""))
    return "\n".join(pieces)


PREFIX = r'''
#define _POSIX_C_SOURCE 200809L
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#define VKD3D_PATH_MAX 4096
typedef struct { char extensionName[256]; uint32_t specVersion; } VkExtensionProperties;
static unsigned int env_reads, warnings, cases;
#define WARN(...) (++warnings)
'''

ENV_WRAPPER = r'''
static bool vkd3d_get_env_var(const char *name, char *value, size_t size)
{
    ++env_reads;
    return platform_get_env_var(name, value, size);
}
'''

TESTS = r'''
static void set_disabled_extensions(const char *value)
{
#ifdef _WIN32
    assert(!_putenv_s("VKD3D_DISABLE_EXTENSIONS", value ? value : ""));
#else
    if (value) assert(!setenv("VKD3D_DISABLE_EXTENSIONS", value, 1));
    else assert(!unsetenv("VKD3D_DISABLE_EXTENSIONS"));
#endif
}

static void check(const char *disabled, const VkExtensionProperties *extensions,
        unsigned int count, const char *name, uint32_t version, bool expected)
{
    set_disabled_extensions(disabled);
    env_reads = warnings = 0;
    assert(has_extension(extensions, count, name, version) == expected);
    assert(env_reads == !!count);
    assert(warnings == (unsigned int)(count && disabled &&
            vkd3d_debug_list_has_member(disabled, name)));
#ifdef HAVE_BASELINE
    assert(baseline_has_extension(extensions, count, name, version) == expected);
#endif
    ++cases;
}

static void test_lookup(void)
{
    const VkExtensionProperties extensions[] =
    {
        {"VK_EXT_first", 1}, {"VK_EXT_middle", 2},
        {"VK_EXT_duplicate", 1}, {"VK_EXT_duplicate", 3}, {"VK_EXT_last", 5},
    };
    const char *disabled[] = {NULL, "", "VK_EXT_other", "VK_EXT_middle", "VK_EXT_last"};
    unsigned int i, j, version;

    check(NULL, NULL, 0, "VK_EXT_first", 0, false);
    check("VK_EXT_first", NULL, 0, "VK_EXT_first", 0, false);
    check(NULL, extensions, 1, "VK_EXT_first", 1, true);
    check(NULL, extensions, 1, "VK_EXT_first", 2, false);
    check("VK_EXT_first", extensions, 1, "VK_EXT_first", 1, false);
    check(NULL, extensions, 1, "VK_EXT_missing", 0, false);
    check(NULL, extensions, 5, "VK_EXT_last", UINT32_MAX, false);
    for (i = 0; i < sizeof(disabled) / sizeof(*disabled); ++i)
    {
        for (j = 0; j < sizeof(extensions) / sizeof(*extensions); ++j)
        {
            for (version = 0; version <= 6; ++version)
            {
                uint32_t available = j == 2 ? 3 : extensions[j].specVersion;
                bool blocked = disabled[i] && vkd3d_debug_list_has_member(
                        disabled[i], extensions[j].extensionName);
                check(disabled[i], extensions, 5, extensions[j].extensionName,
                        version, !blocked && available >= version);
            }
        }
        check(disabled[i], extensions, 5, "VK_EXT_missing", 0, false);
    }
    check("VK_EXT_other,VK_EXT_middle", extensions, 5, "VK_EXT_middle", 2, false);
    check("VK_EXT_middle;VK_EXT_other", extensions, 5, "VK_EXT_middle", 2, false);
    check("VK_EXT_middle_suffix", extensions, 5, "VK_EXT_middle", 2, true);
    check("prefix_VK_EXT_middle", extensions, 5, "VK_EXT_middle", 2, true);
    check("VK_EXT_MIDDLE", extensions, 5, "VK_EXT_middle", 2, true);
    check("VK_EXT_missing", extensions, 5, "VK_EXT_missing", 0, false);
    /* Each invocation observes changes. No process-wide environment cache. */
    check("VK_EXT_middle", extensions, 5, "VK_EXT_middle", 2, false);
    check(NULL, extensions, 5, "VK_EXT_middle", 2, true);
    printf("%u lookup cases passed (results, environment reads, warning counts).\n", cases);
}
'''

BENCHMARK = r'''
static double now(void)
{
    struct timespec t;
    assert(!clock_gettime(CLOCK_MONOTONIC, &t));
    return (double)t.tv_sec + (double)t.tv_nsec / 1e9;
}

static void benchmark(void)
{
    VkExtensionProperties extensions[250];
    char names[60][256];
    const char *disabled[] = {NULL, "VK_EXT_test_0249", "VK_EXT_other;VK_EXT_test_0100;VK_EXT_test_0249"};
    unsigned int scenario, repeat, variant, pass, i;
    volatile unsigned int found = 0;

    for (i = 0; i < 250; ++i)
    {
        snprintf(extensions[i].extensionName, sizeof(extensions[i].extensionName), "VK_EXT_test_%04u", i);
        extensions[i].specVersion = 2;
    }
    for (i = 0; i < 60; ++i)
        snprintf(names[i], sizeof(names[i]), "VK_EXT_test_%04u", i < 56 ? i * 4 : 249 + i - 56);
    for (scenario = 0; scenario < 3; ++scenario)
    {
        set_disabled_extensions(disabled[scenario]);
        for (repeat = 0; repeat < 5; ++repeat)
        {
            for (variant = 0; variant < 2; ++variant)
            {
                bool current = (variant + repeat) % 2;
                double start, elapsed;
                env_reads = warnings = 0;
                start = now();
                for (pass = 0; pass < 1000; ++pass)
                    for (i = 0; i < 60; ++i)
                        found += current ? has_extension(extensions, 250, names[i], 2)
                                : baseline_has_extension(extensions, 250, names[i], 2);
                elapsed = now() - start;
                printf("benchmark scenario=%u repeat=%u variant=%s ns/query=%.2f env/query=%.3f warnings=%u\n",
                        scenario, repeat, current ? "current" : "baseline", elapsed * 1e9 / 60000,
                        (double)env_reads / 60000, warnings);
            }
        }
    }
    assert(found);
}
'''

MAIN = r'''
int main(int argc, char **argv)
{
    test_lookup();
    if (argc > 1 && !strcmp(argv[1], "--benchmark"))
    {
        @BENCHMARK@
    }
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "libs/vkd3d/device.c")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--generate", type=Path)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--benchmark", action="store_true")
    args = parser.parse_args()
    if args.benchmark and not args.baseline:
        parser.error("--benchmark requires a pre-change --baseline device.c")
    source = generate(args.source, args.baseline)
    if args.generate:
        args.generate.write_text(source)
        return
    build = args.build_dir or Path(tempfile.mkdtemp(prefix="vkd3d-extension-lookup-"))
    build.mkdir(parents=True, exist_ok=True)
    generated = build / "extension_lookup.c"
    binary = build / "extension_lookup"
    generated.write_text(source)
    subprocess.run(shlex.split(os.environ.get("CC", "cc")) + ["-std=c99", "-O2", "-Wall",
                   "-Wextra", "-Werror", "-UNDEBUG", str(generated), "-o", str(binary)], check=True)
    subprocess.run([str(binary)] + (["--benchmark"] if args.benchmark else []), check=True)
    print("Artifacts:", build)


if __name__ == "__main__":
    main()
