procedure Body;
var D, B, It, O: Variant; S: String; R: TCoordRect;
begin
  D := Client.OpenDocument('PCB', '%OUT%\batch\One-Air-Max\One-Air-Max.PcbDoc');
  B := PCBServer.GetPCBBoardByPath('%OUT%\batch\One-Air-Max\One-Air-Max.PcbDoc');
  It := B.SpatialIterator_Create;
  It.AddFilter_ObjectSet(AllObjects);
  It.AddFilter_LayerSet(MkSet(eTopLayer, eMultiLayer, eKeepOutLayer));
  It.AddFilter_Area(84580235, 63328088, 86706219, 73544624);
  O := It.FirstPCBObject;
  while O <> Nil do
  begin
    R := O.BoundingRectangle;
    S := IntToStr(O.ObjectId) + ' ' + Layer2String(O.Layer);
    if O.Net <> Nil then S := S + ' net=' + O.Net.Name;
    if O.InComponent then S := S + ' comp=' + O.Component.Name.Text;
    if O.InPolygon then S := S + ' poly=' + O.Polygon.Name;
    try if O.IsKeepout then S := S + ' KEEPOUT'; except end;
    S := S + ' bbox=' + IntToStr(CoordToMils(R.Left)) + ',' + IntToStr(CoordToMils(R.Bottom)) + '-' + IntToStr(CoordToMils(R.Right)) + ',' + IntToStr(CoordToMils(R.Top));
    L(S + ' ' + O.Descriptor);
    O := It.NextPCBObject;
  end;
  B.SpatialIterator_Destroy(It);
end;
