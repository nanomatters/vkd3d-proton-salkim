uint2 main(uint layer : SV_RenderTargetArrayIndex, uint viewport : SV_ViewportArrayIndex) : SV_Target
{
    return uint2(layer, viewport);
}
