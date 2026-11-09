struct Vertex
{
    float4 position : SV_Position;
    uint layer : SV_RenderTargetArrayIndex;
    uint viewport : SV_ViewportArrayIndex;
};

struct Patch
{
    float edges[3] : SV_TessFactor;
    float inside : SV_InsideTessFactor;
};

[domain("tri")]
Vertex main(OutputPatch<Vertex, 3> input, float3 uv : SV_DomainLocation, Patch patch)
{
    Vertex output;
    output.position = input[0].position * uv.x + input[1].position * uv.y + input[2].position * uv.z;
    output.layer = input[0].layer;
    output.viewport = input[1].viewport;
    return output;
}
