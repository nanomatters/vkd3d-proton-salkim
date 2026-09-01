/*
 * Copyright 2026 Erhan Bilgili
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation; either
 * version 2.1 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
 * Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public
 * License along with this library; if not, write to the Free Software
 * Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301, USA
 */

#ifndef VKD3D_SWAPCHAIN_HUD_H
#define VKD3D_SWAPCHAIN_HUD_H

#include "vkd3d_private.h"

#define VKD3D_SWAPCHAIN_HUD_MAX_VERTICES 4096

struct vkd3d_swapchain_hud_buffer
{
    VkBuffer vk_buffer;
    struct vkd3d_device_memory_allocation memory;
    void *mapped;
    VkDeviceSize size;
};

struct vkd3d_swapchain_hud
{
    struct vkd3d_swapchain_hud_buffer vertex_buffers[DXGI_MAX_SWAP_CHAIN_BUFFERS];
    struct vkd3d_swapchain_hud_buffer font_buffer;
    uint32_t font_width;
    uint32_t font_height;
    bool failed;
};

void vkd3d_swapchain_hud_cleanup(struct vkd3d_swapchain_hud *hud,
        struct d3d12_device *device);

bool vkd3d_swapchain_hud_record(struct vkd3d_swapchain_hud *hud,
        struct d3d12_device *device, VkCommandBuffer vk_cmd, uint32_t swapchain_index,
        VkFormat format, uint32_t width, uint32_t height, DXGI_COLOR_SPACE_TYPE color_space,
        const DXGI_VK_HUD_VERTEX *vertices, uint32_t vertex_count, float scale, float opacity,
        const uint8_t *font_data, uint32_t font_data_size, uint32_t font_width, uint32_t font_height);

#endif
