procedure Body;
var D, B, It, O: Variant; X, Y, R: Integer; S: String;
begin
  D := Client.OpenDocument('PCB', '%OUT%\batch\board\board.PcbDoc');
  B := PCBServer.GetPCBBoardByPath('%OUT%\batch\board\board.PcbDoc');
  X := 52975783; Y := 61648393; R := MilsToCoord(45);
  It := B.SpatialIterator_Create;
  It.AddFilter_ObjectSet(AllObjects);
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Area(X - R, Y - R, X + R, Y + R);
  O := It.FirstPCBObject;
  while O <> Nil do
  begin
    S := IntToStr(O.ObjectId) + ' layer=' + Layer2String(O.Layer);
    if O.Net <> Nil then S := S + ' net=' + O.Net.Name;
    if O.InComponent then S := S + ' comp=' + O.Component.Name.Text;
    if O.InPolygon then S := S + ' inpoly';
    try S := S + ' kind=' + IntToStr(O.Kind); except end;
    L(S + ' ' + O.Descriptor);
    O := It.NextPCBObject;
  end;
  B.SpatialIterator_Destroy(It);
end;
