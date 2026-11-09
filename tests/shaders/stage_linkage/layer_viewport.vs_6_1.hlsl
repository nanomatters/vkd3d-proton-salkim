struct Vertex
{
    float4 position : SV_Position;
    uint layer : SV_RenderTargetArrayIndex;
    uint viewport : SV_ViewportArrayIndex;
};

Vertex main(uint id : SV_VertexID)
{
    Vertex output;
    output.position = float4(id & 1, id >> 1, 0, 1);
    output.layer = id + 17;
    output.viewport = id + 23;
    return output;
}
