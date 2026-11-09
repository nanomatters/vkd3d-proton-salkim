struct Vertex
{
    float4 position : SV_Position;
    uint layer : SV_RenderTargetArrayIndex;
    uint viewport : SV_ViewportArrayIndex;
};

[maxvertexcount(3)]
void main(triangle Vertex input[3], inout TriangleStream<Vertex> output)
{
    for (uint i = 0; i < 3; i++)
        output.Append(input[i]);
}
