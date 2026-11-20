function FindPad(B: Variant; Des, Num: String): Variant;
var It, O: Variant;
begin
  Result := Nil;
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(ePadObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  O := It.FirstPCBObject;
  while O <> Nil do
  begin
    if O.InComponent then
      if (O.Component.Name.Text = Des) and (O.Name = Num) then begin Result := O; Break; end;
    O := It.NextPCBObject;
  end;
  B.BoardIterator_Destroy(It);
end;

procedure Body;
var D, B, It, P, G, GI, R: Variant; i, best, dist, nreg: Integer; Des: String; Lst: TStringList;
begin
  D := Client.OpenDocument('PCB', '%OUT%\batch\board\board.PcbDoc');
  D.SetModified(False);
  Client.CloseDocument(D);
  D := Client.OpenDocument('PCB', '%OUT%\batch\board\board.PcbDoc');
  Client.ShowDocument(D);
  B := PCBServer.GetCurrentPCBBoard;
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(ePolyObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  G := It.FirstPCBObject;
  while (G <> Nil) and (G.Name <> 'GND_BCu') do G := It.NextPCBObject;
  B.BoardIterator_Destroy(It);
  L('poly ' + G.Name + ' pourindex ' + IntToStr(G.PourIndex));
  Lst := TStringList.Create;
  Lst.CommaText := 'C23,C24,C27,C13,D25,C30';
  for i := 0 to Lst.Count - 1 do
  begin
    Des := Lst[i];
    if Des = 'D25' then P := FindPad(B, Des, '1') else P := FindPad(B, Des, '2');
    best := 999999999; nreg := 0;
    GI := G.GroupIterator_Create;
    GI.AddFilter_ObjectSet(MkSet(eRegionObject, eTrackObject, eArcObject));
    R := GI.FirstPCBObject;
    while R <> Nil do
    begin
      nreg := nreg + 1;
      dist := B.PrimPrimDistance(P, R);
      if dist < best then best := dist;
      R := GI.NextPCBObject;
    end;
    G.GroupIterator_Destroy(GI);
    L(Des + ' pad net=' + P.Net.Name + ' minDist(mil)=' + FloatToStr(CoordToMils(best)) + ' parts=' + IntToStr(nreg));
  end;
  D.SetModified(False);
end;
