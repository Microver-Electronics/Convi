procedure Body;
var D, B, It, G: Variant; X, Y: Integer;
begin
  D := Client.OpenDocument('PCB', '%OUT%\batch\board\board.PcbDoc');
  B := PCBServer.GetPCBBoardByPath('%OUT%\batch\board\board.PcbDoc');
  X := 52975783 + MilsToCoord(18); Y := 61648393 + MilsToCoord(18);
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(ePolyObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  G := It.FirstPCBObject;
  while G <> Nil do
  begin
    L(G.Name + ' layer=' + Layer2String(G.Layer) + ' inside=' + BoolToStr(G.PointInPolygon(X, Y), True) +
      ' pts=' + IntToStr(G.PointCount) + ' idx=' + IntToStr(G.PourIndex) + ' remDead=' + BoolToStr(G.RemoveDead, True) +
      ' necks=' + BoolToStr(G.RemoveNarrowNecks, True) + ' neckW=' + FloatToStr(CoordToMils(G.NeckWidthThreshold)) +
      ' minTrack=' + FloatToStr(CoordToMils(G.MinTrack)));
    G := It.NextPCBObject;
  end;
  B.BoardIterator_Destroy(It);
end;
