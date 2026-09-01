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

#define VKD3D_DBG_CHANNEL VKD3D_DBG_CHANNEL_API

#include "swapchain_hud.h"

STATIC_ASSERT(sizeof(DXGI_VK_HUD_VERTEX) == 24);

static void vkd3d_swapchain_hud_buffer_cleanup(struct vkd3d_swapchain_hud_buffer *buffer,
        struct d3d12_device *device)
{
    const struct vkd3d_vk_device_procs *vk_procs = &device->vk_procs;

    if (buffer->mapped)
        VK_CALL(vkUnmapMemory(device->vk_device, buffer->memory.vk_memory));
    VK_CALL(vkDestroyBuffer(device->vk_device, buffer->vk_buffer, NULL));
    if (buffer->memory.vk_memory)
        vkd3d_free_device_memory(device, &buffer->memory);
    memset(buffer, 0, sizeof(*buffer));
}

static HRESULT vkd3d_swapchain_hud_buffer_init(struct vkd3d_swapchain_hud_buffer *buffer,
        struct d3d12_device *device, VkDeviceSize size, const char *tag)
{
    const struct vkd3d_vk_device_procs *vk_procs = &device->vk_procs;
    HRESULT hr;
    VkResult vr;

    if (FAILED(hr = vkd3d_create_buffer_explicit_usage(device,
            VK_BUFFER_USAGE_2_STORAGE_BUFFER_BIT_KHR, size, tag, &buffer->vk_buffer)))
        return hr;

    if (FAILED(hr = vkd3d_allocate_internal_buffer_memory(device, buffer->vk_buffer,
            VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
            0, &buffer->memory)))
        goto fail;

    if ((vr = VK_CALL(vkMapMemory(device->vk_device, buffer->memory.vk_memory,
            0, VK_WHOLE_SIZE, 0, &buffer->mapped))) < 0)
    {
        hr = hresult_from_vk_result(vr);
        goto fail;
    }

    buffer->size = size;
    return S_OK;

fail:
    vkd3d_swapchain_hud_buffer_cleanup(buffer, device);
    return hr;
}

static HRESULT vkd3d_swapchain_hud_ensure_font(struct vkd3d_swapchain_hud *hud,
        struct d3d12_device *device, const uint8_t *data, uint32_t size,
        uint32_t width, uint32_t height)
{
    VkDeviceSize buffer_size;
    HRESULT hr;

    if (hud->font_buffer.vk_buffer)
        return hud->font_width == width && hud->font_height == height ? S_OK : E_INVALIDARG;

    buffer_size = (size + 3u) & ~3u;
    if (FAILED(hr = vkd3d_swapchain_hud_buffer_init(&hud->font_buffer,
            device, buffer_size, "swapchain-hud-font")))
        return hr;

    memcpy(hud->font_buffer.mapped, data, size);
    memset((uint8_t *)hud->font_buffer.mapped + size, 0, buffer_size - size);
    hud->font_width = width;
    hud->font_height = height;
    return S_OK;
}

static HRESULT vkd3d_swapchain_hud_ensure_vertices(struct vkd3d_swapchain_hud *hud,
        struct d3d12_device *device, uint32_t index)
{
    VkDeviceSize size = VKD3D_SWAPCHAIN_HUD_MAX_VERTICES * sizeof(DXGI_VK_HUD_VERTEX);

    if (hud->vertex_buffers[index].vk_buffer)
        return S_OK;

    return vkd3d_swapchain_hud_buffer_init(&hud->vertex_buffers[index],
            device, size, "swapchain-hud-vertices");
}

static uint32_t vkd3d_swapchain_hud_output_mode(VkFormat format,
        DXGI_COLOR_SPACE_TYPE color_space)
{
    if (color_space == DXGI_COLOR_SPACE_RGB_FULL_G10_NONE_P709)
        return 1;
    if (color_space == DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020)
        return 2;

    switch (format)
    {
        case VK_FORMAT_R8G8B8A8_SRGB:
        case VK_FORMAT_B8G8R8A8_SRGB:
        case VK_FORMAT_A8B8G8R8_SRGB_PACK32:
            return 3;
        default:
            return 0;
    }
}

void vkd3d_swapchain_hud_cleanup(struct vkd3d_swapchain_hud *hud,
        struct d3d12_device *device)
{
    unsigned int i;

    for (i = 0; i < ARRAY_SIZE(hud->vertex_buffers); i++)
        vkd3d_swapchain_hud_buffer_cleanup(&hud->vertex_buffers[i], device);
    vkd3d_swapchain_hud_buffer_cleanup(&hud->font_buffer, device);
}

bool vkd3d_swapchain_hud_record(struct vkd3d_swapchain_hud *hud,
        struct d3d12_device *device, VkCommandBuffer vk_cmd, uint32_t swapchain_index,
        VkFormat format, uint32_t width, uint32_t height, DXGI_COLOR_SPACE_TYPE color_space,
        const DXGI_VK_HUD_VERTEX *vertices, uint32_t vertex_count, float scale, float opacity,
        const uint8_t *font_data, uint32_t font_data_size, uint32_t font_width, uint32_t font_height)
{
    const struct vkd3d_vk_device_procs *vk_procs = &device->vk_procs;
    struct vkd3d_swapchain_hud_push_constants push_constants;
    struct vkd3d_swapchain_hud_buffer *vertex_buffer;
    struct vkd3d_swapchain_hud_info pipeline;
    VkDescriptorBufferInfo buffer_info[2];
    VkWriteDescriptorSet writes[2];
    VkViewport viewport;
    VkRect2D scissor;
    HRESULT hr;

    if (!vertex_count || hud->failed)
        return false;

    if (swapchain_index >= ARRAY_SIZE(hud->vertex_buffers) ||
            vertex_count > VKD3D_SWAPCHAIN_HUD_MAX_VERTICES)
        return false;

    if (FAILED(hr = vkd3d_swapchain_hud_ensure_font(hud, device,
            font_data, font_data_size, font_width, font_height)) ||
            FAILED(hr = vkd3d_swapchain_hud_ensure_vertices(hud, device, swapchain_index)) ||
            FAILED(hr = vkd3d_meta_get_swapchain_hud_pipeline(&device->meta_ops, format, &pipeline)))
    {
        WARN("Failed to prepare swapchain HUD, hr %#lx.\n", (unsigned long)hr);
        hud->failed = true;
        return false;
    }

    vertex_buffer = &hud->vertex_buffers[swapchain_index];
    memcpy(vertex_buffer->mapped, vertices, vertex_count * sizeof(*vertices));

    memset(buffer_info, 0, sizeof(buffer_info));
    buffer_info[0].buffer = vertex_buffer->vk_buffer;
    buffer_info[0].range = vertex_count * sizeof(*vertices);
    buffer_info[1].buffer = hud->font_buffer.vk_buffer;
    buffer_info[1].range = hud->font_buffer.size;

    memset(writes, 0, sizeof(writes));
    writes[0].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
    writes[0].dstBinding = 0;
    writes[0].descriptorCount = 1;
    writes[0].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
    writes[0].pBufferInfo = &buffer_info[0];
    writes[1].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
    writes[1].dstBinding = 1;
    writes[1].descriptorCount = 1;
    writes[1].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
    writes[1].pBufferInfo = &buffer_info[1];

    memset(&push_constants, 0, sizeof(push_constants));
    push_constants.surface_size[0] = width;
    push_constants.surface_size[1] = height;
    push_constants.opacity = opacity;
    push_constants.scale = scale;
    push_constants.output_mode = vkd3d_swapchain_hud_output_mode(format, color_space);
    push_constants.font_width = font_width;
    push_constants.font_height = font_height;

    memset(&viewport, 0, sizeof(viewport));
    viewport.width = width;
    viewport.height = height;
    viewport.maxDepth = 1.0f;
    memset(&scissor, 0, sizeof(scissor));
    scissor.extent.width = width;
    scissor.extent.height = height;

    VK_CALL(vkCmdSetViewport(vk_cmd, 0, 1, &viewport));
    VK_CALL(vkCmdSetScissor(vk_cmd, 0, 1, &scissor));
    VK_CALL(vkCmdBindPipeline(vk_cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline.vk_pipeline));
    VK_CALL(vkCmdPushDescriptorSetKHR(vk_cmd, VK_PIPELINE_BIND_POINT_GRAPHICS,
            pipeline.vk_pipeline_layout, 0, ARRAY_SIZE(writes), writes));
    VK_CALL(vkCmdPushConstants(vk_cmd, pipeline.vk_pipeline_layout,
            VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT,
            0, sizeof(push_constants), &push_constants));
    VK_CALL(vkCmdDraw(vk_cmd, vertex_count, 1, 0, 0));
    return true;
}
