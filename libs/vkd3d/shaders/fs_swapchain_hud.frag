// Copyright 2026 Erhan Bilgili (local adaptations)
// SPDX-License-Identifier: LGPL-2.1-or-later AND Zlib
//
// Adapted from DXVK's hud_text_frag.frag, hud_frag_common.glsl and
// dxvk_color_space.glsl. Local changes use a storage-buffer font atlas and
// push-constant output selection. The original DXVK license follows.
/*
                  Copyright (c) 2017 Philip Rebohle
                  Copyright (c) 2019 Joshua Ashton
                  Copyright (c) 2019 Robin Kertels
                  Copyright (c) 2023 Jeffrey Ellison

                          zlib/libpng license

This software is provided 'as-is', without any express or implied
warranty. In no event will the authors be held liable for any damages
arising from the use of this software.

Permission is granted to anyone to use this software for any purpose,
including commercial applications, and to alter it and redistribute it
freely, subject to the following restrictions:

– The origin of this software must not be misrepresented; you must not
  claim that you wrote the original software. If you use this software
  in a product, an acknowledgment in the product documentation would be
  appreciated but is not required.

– Altered source versions must be plainly marked as such, and must not
  be misrepresented as being the original software.

– This notice may not be removed or altered from any source distribution.
*/

#version 450

layout(set = 0, binding = 1, std430)
readonly buffer font_buffer_t
{
    uint font_data[];
};

layout(push_constant)
uniform push_data_t
{
    uvec2 surface_size;
    float opacity;
    float scale;
    uint output_mode;
    uint font_width;
    uint font_height;
};

layout(location = 0) in vec2 v_texcoord;
layout(location = 1) in vec4 v_color;

layout(location = 0) out vec4 frag_color;

float load_font_texel(ivec2 coord)
{
    coord = clamp(coord, ivec2(0), ivec2(font_width, font_height) - 1);
    uint index = uint(coord.y) * font_width + uint(coord.x);
    uint word = font_data[index >> 2];
    return float((word >> ((index & 3) * 8)) & 0xff) / 255.0f;
}

float sample_font(vec2 coord)
{
    vec2 texel = coord - 0.5f;
    ivec2 base = ivec2(floor(texel));
    vec2 weight = fract(texel);
    float x0 = mix(load_font_texel(base), load_font_texel(base + ivec2(1, 0)), weight.x);
    float x1 = mix(load_font_texel(base + ivec2(0, 1)), load_font_texel(base + ivec2(1, 1)), weight.x);
    return mix(x0, x1, weight.y);
}

float sample_alpha(float bias)
{
    float value = sample_font(v_texcoord) + bias - 0.5f;
    float dist = value * dot(vec2(5.0f), 1.0f / fwidth(v_texcoord));
    return clamp(dist + 0.5f, 0.0f, 1.0f);
}

vec3 linear_to_srgb(vec3 value)
{
    vec3 lo = value * 12.92f;
    vec3 hi = pow(value, vec3(5.0f / 12.0f)) * 1.055f - 0.055f;
    return mix(hi, lo, lessThanEqual(value, vec3(0.0031308f)));
}

vec3 nits_to_pq(vec3 nits)
{
    const float c1 = 0.8359375f;
    const float c2 = 18.8515625f;
    const float c3 = 18.6875f;
    const float m1 = 0.1593017578125f;
    const float m2 = 78.84375f;
    vec3 value = clamp(nits / 10000.0f, vec3(0.0f), vec3(1.0f));
    vec3 value_m1 = pow(value, vec3(m1));
    return pow((c1 + c2 * value_m1) / (1.0f + c3 * value_m1), vec3(m2));
}

vec3 rec709_to_rec2020(vec3 color)
{
    return vec3(
        dot(color, vec3(0.6274039f, 0.3292830f, 0.0433131f)),
        dot(color, vec3(0.0690973f, 0.9195404f, 0.0113623f)),
        dot(color, vec3(0.0163914f, 0.0880133f, 0.8955953f)));
}

void main()
{
    float center_alpha = sample_alpha(0.0f);
    float shadow_alpha = sample_alpha(0.3f);
    vec4 color;

    color.rgb = v_color.rgb * center_alpha;
    color.a = shadow_alpha * v_color.a * opacity;
    color.rgb *= color.a;

    if (output_mode == 0)
        color.rgb = linear_to_srgb(color.rgb);
    else if (output_mode == 1)
        color.rgb *= 203.0f / 80.0f;
    else if (output_mode == 2)
        color.rgb = nits_to_pq(rec709_to_rec2020(color.rgb) * 203.0f);

    frag_color = color;
}
