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

Patch constants(InputPatch<Vertex, 3> input)
{
    Patch output;
    output.edges[0] = output.edges[1] = output.edges[2] = 1;
    output.inside = 1;
    return output;
}

[domain("tri")]
[outputcontrolpoints(3)]
[partitioning("integer")]
[outputtopology("triangle_ccw")]
[patchconstantfunc("constants")]
Vertex main(InputPatch<Vertex, 3> input, uint id : SV_OutputControlPointID)
{
    return input[id];
}
