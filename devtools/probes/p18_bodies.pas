procedure Body;
var B, It, C, Bd: Variant; R: TCoordRect; cx, cy, d: Integer; n: Integer;
begin
  B := PCBServer.GetCurrentPCBBoard;
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eComponentObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  C := It.FirstPCBObject;
  n := 0;
  while C <> Nil do
  begin
    Bd := C.GroupIterator_Create;
    Bd.AddFilter_ObjectSet(MkSet(eComponentBodyObject));
    R := Nil;
    Bd := Bd.FirstPCBObject;
    if Bd <> Nil then
    begin
      R := Bd.BoundingRectangle;
      cx := (R.Left + R.Right) div 2; cy := (R.Bottom + R.Top) div 2;
      d := Abs(cx - C.X) + Abs(cy - C.Y);
      if d > MMsToCoord(8) then
        L(C.Name.Text + ' body center off ' + FloatToStr(CoordToMMs(cx - C.X)) + ',' + FloatToStr(CoordToMMs(cy - C.Y)) + ' mm  rot=' + FloatToStr(C.Rotation) + ' layer=' + IntToStr(C.Layer) + ' h=' + FloatToStr(CoordToMMs(Bd.OverallHeight)));
      n := n + 1;
    end;
    C := It.NextPCBObject;
  end;
  B.BoardIterator_Destroy(It);
  L('components with body: ' + IntToStr(n));
end;
