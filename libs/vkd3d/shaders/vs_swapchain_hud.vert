// Copyright 2026 Erhan Bilgili

#version 450

struct hud_vertex_t
{
    vec2 position;
    vec2 texcoord;
    uint color;
    uint padding;
};

layout(set = 0, binding = 0, std430)
readonly buffer vertex_buffer_t
{
    hud_vertex_t vertices[];
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

layout(location = 0) out vec2 v_texcoord;
layout(location = 1) out vec4 v_color;

void main()
{
    hud_vertex_t vertex = vertices[gl_VertexIndex];
    vec2 position = vertex.position * scale;

    gl_Position = vec4(
        2.0f * position.x / float(surface_size.x) - 1.0f,
        2.0f * position.y / float(surface_size.y) - 1.0f,
        0.0f, 1.0f);
    v_texcoord = vertex.texcoord;
    v_color = unpackUnorm4x8(vertex.color);
}
