#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Record the actual swapchain HUD update and render functions without a GPU.

The two production functions are extracted unchanged. Vulkan command recorders
check attachment loads and access masks, while the real SetHudData initializes
the font and frame state. Queue publication is represented by copying the frame
snapshot, not by simulating threads or claiming to test queue synchronization.
The recorder accepts both HUD signatures, before and after layout_extent.
Use --revision bf0e9044 as an unfixed negative control and --generate for Meson.
Standalone artifacts are retained under ~/tmp unless --build-dir is supplied.
"""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CASES = ("empty", "publication", "barrier", "direct", "blank", "failed", "queued")


def function(source, name):
    match = re.search(r"^static (?:HRESULT STDMETHODCALLTYPE|void) " + name
                      + r"\([^;]+?\)\s*\{", source, re.M)
    if not match:
        raise ValueError("Missing source function: " + name)
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


def generate(source):
    return HARNESS.replace("@FUNCTIONS@", "\n\n".join(function(source, name) for name in (
        "dxgi_vk_swap_chain_SetHudData", "dxgi_vk_swap_chain_record_render_pass")))


HARNESS = r'''
#include <assert.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>

typedef int32_t HRESULT;
typedef struct { float Position[2], Texcoord[2]; uint32_t Color, Padding; } DXGI_VK_HUD_VERTEX;
typedef struct
{
    uint32_t StructSize, VertexCount;
    const DXGI_VK_HUD_VERTEX *pVertices;
    float Scale, Opacity;
    uint32_t FontWidth, FontHeight, FontDataSize;
    const uint8_t *pFontData;
} DXGI_VK_HUD_DATA;
#define STDMETHODCALLTYPE
#define S_OK 0
#define E_INVALIDARG ((HRESULT)0x80070057)
#define E_OUTOFMEMORY ((HRESULT)0x8007000e)
#define VKD3D_SWAPCHAIN_HUD_MAX_VERTICES 16384
#define DXGI_SCALING_NONE 1
#define TRACE(...) ((void)0)
#define WARN(...) ((void)0)
#define VK_CALL(call) (call)
#define VKD3D_CONFIG_FLAG_IS_SET(flag) 0
#define vkd3d_atomic_uint32_load_explicit(value, order) (*(value))
#define vkd3d_malloc malloc

struct vkd3d_vk_device_procs { int unused; };
struct mock_device
{
    struct vkd3d_vk_device_procs vk_procs;
    struct { bool EXT_debug_utils; } vk_info;
};
struct mock_queue { struct mock_device *device; };
struct d3d12_resource
{
    uint32_t initial_layout_transition;
    VkImageLayout common_layout;
    struct { VkImage vk_image; } res;
};
struct hud_frame
{
    DXGI_VK_HUD_VERTEX *vertices;
    size_t vertices_size;
    uint32_t vertex_count;
    VkExtent2D layout_extent;
    float scale, opacity;
};
struct dxgi_vk_swap_chain
{
    struct mock_queue *queue;
    struct { uint32_t Width, Height, Scaling; } desc;
    struct
    {
        struct d3d12_resource *backbuffers[1];
        VkImageView vk_image_views[1];
        struct hud_frame hud;
    } user;
    struct { uint32_t user_index, dxgi_color_space_type; struct hud_frame hud; } request;
    struct
    {
        struct { bool failed; } renderer;
        uint8_t *font_data;
        uint32_t font_data_size, font_width, font_height;
        bool enabled;
    } hud;
    struct
    {
        VkImage vk_backbuffer_images[1];
        VkImageView vk_backbuffer_image_views[1];
        uint32_t backbuffer_width, backbuffer_height;
        VkFormat backbuffer_format;
        struct { VkPipeline vk_pipeline; VkPipelineLayout vk_pipeline_layout; } pipeline;
    } present;
};
typedef struct dxgi_vk_swap_chain IDXGIVkSwapChainHud;
#define impl_from_IDXGIVkSwapChain(iface) (iface)
#define d3d12_device_get_rendering_flags(device) 0

static struct mock_device device;
static struct mock_queue queue = { &device };
static struct d3d12_resource backbuffer;
static struct dxgi_vk_swap_chain chain;
static unsigned int barrier_count, rendering_count, hud_count, blit_count, draw_count;
static bool rendering;
static VkImageMemoryBarrier2 barriers[3][2];
static uint32_t barrier_sizes[3];
static VkAttachmentLoadOp loads[2];

static bool vkd3d_array_reserve(void **memory, size_t *size, size_t count, size_t stride)
{
    void *allocation;
    if (count <= *size)
        return true;
    if (!(allocation = realloc(*memory, count * stride)))
        return false;
    *memory = allocation;
    *size = count;
    return true;
}

static void record_barrier(const VkDependencyInfo *info)
{
    assert(!rendering && barrier_count < 3 && info->imageMemoryBarrierCount <= 2);
    barrier_sizes[barrier_count] = info->imageMemoryBarrierCount;
    memcpy(barriers[barrier_count++], info->pImageMemoryBarriers,
            info->imageMemoryBarrierCount * sizeof(*info->pImageMemoryBarriers));
}

static void begin_rendering(const VkRenderingInfo *info)
{
    assert(!rendering && rendering_count < 2 && info->colorAttachmentCount == 1);
    assert(info->pColorAttachments->imageLayout == VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL);
    loads[rendering_count++] = info->pColorAttachments->loadOp;
    rendering = true;
}

static void end_rendering(void) { assert(rendering); rendering = false; }
static void record_hud(void) { assert(rendering); hud_count++; }
static void record_blit(void) { assert(!rendering); blit_count++; }
static void record_draw(void) { assert(rendering); draw_count++; }
#define vkCmdPipelineBarrier2(cmd, info) record_barrier(info)
#define vkCmdBeginRendering(cmd, info) begin_rendering(info)
#define vkCmdEndRendering(cmd) end_rendering()
#define vkCmdBlitImage(...) record_blit()
#define vkCmdDraw(...) record_draw()
#define vkCmdSetViewport(...) ((void)0)
#define vkCmdSetScissor(...) ((void)0)
#define vkCmdBindPipeline(...) ((void)0)
#define vkCmdPushDescriptorSetKHR(...) ((void)0)
#define vkCmdBeginDebugUtilsLabelEXT(...) ((void)0)
#define vkCmdEndDebugUtilsLabelEXT(...) ((void)0)
/* Arguments changed when layout_extent was added. The recorder observes the
 * unchanged caller's decision and render scope, not the HUD renderer itself. */
#define vkd3d_swapchain_hud_record(...) record_hud()

@FUNCTIONS@

static void reset_commands(void)
{
    barrier_count = rendering_count = hud_count = blit_count = draw_count = 0;
    memset(barriers, 0, sizeof(barriers));
    assert(!rendering);
}

static void render(void)
{
    reset_commands();
    dxgi_vk_swap_chain_record_render_pass(&chain, VK_NULL_HANDLE, 0);
    assert(!rendering && barrier_count >= 2);
    assert(barriers[barrier_count - 1][0].newLayout == VK_IMAGE_LAYOUT_PRESENT_SRC_KHR);
}

static void check_no_hud_blit(void)
{
    render();
    assert(!hud_count && !rendering_count && blit_count == 1 && !draw_count);
    assert(barrier_count == 2 && barrier_sizes[0] == 2 && barrier_sizes[1] == 2);
    assert(barriers[0][0].dstAccessMask == VK_ACCESS_2_TRANSFER_WRITE_BIT);
    assert(barriers[1][0].dstAccessMask == VK_ACCESS_2_NONE);
}

static void set_hud(void)
{
    static const DXGI_VK_HUD_VERTEX vertices[3] = { { { 1, 2 }, { 3, 4 }, 5, 0 } };
    static const uint8_t font[4] = { 1, 2, 3, 4 };
    DXGI_VK_HUD_DATA data = { sizeof(data), 3, vertices, 1.25f, 0.75f, 2, 2, sizeof(font), font };
    uint8_t *previous_font = chain.hud.font_data;
    assert(dxgi_vk_swap_chain_SetHudData(&chain, &data) == S_OK);
    assert(chain.hud.font_data && chain.hud.font_data != font);
    assert(!memcmp(chain.hud.font_data, font, sizeof(font)));
    assert(!previous_font || previous_font == chain.hud.font_data);
    assert(chain.user.hud.vertex_count == 3);
    assert(!memcmp(chain.user.hud.vertices, vertices, sizeof(vertices)));
}

static void publish_hud(void)
{
    /* The asynchronous present path supplies its own snapshot. Keeping this
     * explicit avoids mistaking the user's latest HUD data for queued data. */
    chain.request.hud = chain.user.hud;
}

static void test_empty(void)
{
    DXGI_VK_HUD_DATA empty = { .StructSize = sizeof(empty), .Scale = 1.0f, .Opacity = 1.0f };
    check_no_hud_blit();
    assert(dxgi_vk_swap_chain_SetHudData(&chain, &empty) == S_OK);
    assert(!chain.hud.font_data && !chain.user.hud.vertex_count);
    publish_hud();
    check_no_hud_blit();
    assert(dxgi_vk_swap_chain_SetHudData(&chain, NULL) == S_OK);
    check_no_hud_blit();
}

static void test_publication(void)
{
    set_hud();
    /* A producer can initialize the font while an older empty frame retires. */
    check_no_hud_blit();
    publish_hud();
    /* This field is producer-owned and must not gate an already queued HUD. */
    chain.hud.enabled = false;
    render();
    assert(hud_count == 1);
    set_hud();
    render();
    assert(hud_count == 1);
}

static void test_barrier(void)
{
    set_hud();
    publish_hud();
    render();
    assert(hud_count == 1 && blit_count == 1 && !draw_count && rendering_count == 1);
    assert(loads[0] == VK_ATTACHMENT_LOAD_OP_LOAD && barrier_count == 3);
    assert(barrier_sizes[0] == 2 && barrier_sizes[1] == 2 && barrier_sizes[2] == 1);
    assert(barriers[0][0].dstAccessMask == VK_ACCESS_2_TRANSFER_WRITE_BIT);
    assert(barriers[1][0].srcStageMask == VK_PIPELINE_STAGE_2_BLIT_BIT);
    assert(barriers[1][0].srcAccessMask == VK_ACCESS_2_TRANSFER_WRITE_BIT);
    assert(barriers[1][0].dstStageMask == VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT);
    assert(barriers[1][0].dstAccessMask == (VK_ACCESS_2_COLOR_ATTACHMENT_READ_BIT |
            VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT));
    assert(barriers[1][0].oldLayout == VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
    assert(barriers[1][0].newLayout == VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL);
}

static void test_direct(void)
{
    chain.desc.Scaling = DXGI_SCALING_NONE;
    chain.desc.Width = 640;
    render();
    assert(!hud_count && !blit_count && draw_count == 1 && rendering_count == 1);
    assert(loads[0] == VK_ATTACHMENT_LOAD_OP_CLEAR && barrier_count == 2);
    assert(barriers[0][0].dstAccessMask == VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT);
    set_hud();
    publish_hud();
    render();
    assert(hud_count == 1 && !blit_count && draw_count == 1 && rendering_count == 1);
    assert(loads[0] == VK_ATTACHMENT_LOAD_OP_CLEAR && barrier_count == 2);
    assert(barriers[0][0].dstAccessMask == VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT);
}

static void test_blank(void)
{
    backbuffer.initial_layout_transition = 1;
    set_hud();
    publish_hud();
    render();
    assert(hud_count == 1 && !blit_count && !draw_count && rendering_count == 1);
    assert(loads[0] == VK_ATTACHMENT_LOAD_OP_CLEAR && barrier_count == 2);
    assert(barriers[0][0].dstAccessMask == VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT);
}

static void test_failed(void)
{
    set_hud();
    publish_hud();
    chain.hud.renderer.failed = true;
    check_no_hud_blit();
}

static void test_queued(void)
{
    set_hud();
    publish_hud();
    assert(dxgi_vk_swap_chain_SetHudData(&chain, NULL) == S_OK);
    render();
    assert(hud_count == 1);
    publish_hud();
    check_no_hud_blit();
}

int main(int argc, char **argv)
{
    static const struct { const char *name; void (*test)(void); } tests[] = {
        {"empty", test_empty}, {"publication", test_publication}, {"barrier", test_barrier},
        {"direct", test_direct}, {"blank", test_blank}, {"failed", test_failed}, {"queued", test_queued}
    };
    unsigned int i, count = 0;
    assert(argc <= 2);
    for (i = 0; i < sizeof(tests) / sizeof(tests[0]); i++)
    {
        if (argc == 2 && strcmp(argv[1], tests[i].name))
            continue;
        memset(&chain, 0, sizeof(chain));
        memset(&backbuffer, 0, sizeof(backbuffer));
        chain.queue = &queue;
        chain.user.backbuffers[0] = &backbuffer;
        chain.desc.Width = chain.present.backbuffer_width = 1280;
        chain.desc.Height = chain.present.backbuffer_height = 720;
        tests[i].test();
        free(chain.user.hud.vertices);
        free(chain.hud.font_data);
        printf("PASS %s\n", tests[i].name);
        count++;
    }
    assert(count);
    return 0;
}
'''


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--source", type=Path)
    source_group.add_argument("--revision")
    parser.add_argument("--generate", type=Path)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--case", choices=CASES)
    args = parser.parse_args()
    if args.revision:
        source = subprocess.check_output(["git", "show", args.revision + ":libs/vkd3d/swapchain.c"],
                                         cwd=ROOT, text=True)
    else:
        source = (args.source or ROOT / "libs/vkd3d/swapchain.c").read_text()
    harness = generate(source)
    if args.generate:
        args.generate.write_text(harness)
    else:
        base = Path.home() / "tmp"
        base.mkdir(parents=True, exist_ok=True)
        build = args.build_dir or Path(tempfile.mkdtemp(prefix="swapchain-hud-", dir=base))
        build.mkdir(parents=True, exist_ok=True)
        source_path, binary = build / "swapchain_hud.c", build / "swapchain_hud"
        source_path.write_text(harness)
        command = shlex.split(os.environ.get("CC", "cc"))
        command += ["-std=c11", "-O1", "-g", "-UNDEBUG", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-variable", "-Wno-unused-but-set-variable", "-Wno-unused-parameter",
                    "-I", str(ROOT / "khronos/Vulkan-Headers/include")]
        if os.environ.get("SWAPCHAIN_HUD_TEST_SANITIZE") == "1":
            command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
        subprocess.run(command + [str(source_path), "-o", str(binary)], check=True)
        subprocess.run([str(binary)] + ([args.case] if args.case else []), check=True, timeout=10)
        print("Retained harness:", build)
